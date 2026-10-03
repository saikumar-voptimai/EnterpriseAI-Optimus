"""Transactional external-delivery outbox with a persistent 20-second Undo window.

A provider timeout after dispatch has an unknown outcome. Such deliveries require
manual inspection, never blind retries. Providers do not promise exactly-once.
"""

import asyncio
from datetime import timedelta
from email.message import EmailMessage
from email.utils import parseaddr
import smtplib
import ssl
from sqlalchemy import select
from app.models import User, utcnow
from app.models_connections import Delivery
from app.repositories.access import AccessRepository, NotFound, AuthorizationError
from app.services.connections import ConnectionService, slack_webhook
from app.connectors.base import ConnectorHTTP, validate_url
from app.services.errors import ServiceError, ProviderError


def email_address(value):
    name, address = parseaddr(value)
    if (
        name
        or address != value
        or "\r" in value
        or "\n" in value
        or "@" not in address
        or len(value) > 320
    ):
        raise ServiceError("Provide a single email address without a display name.", 422)
    return address


CHANNEL_PROVIDERS = {"teams": "teams_workflow", "slack": "slack_webhook"}


class DeliveryService:
    def __init__(self, db, settings=None, http=None):
        self.db = db
        self.connections = ConnectionService(db, settings, http)
        self.settings, self.http = self.connections.settings, http or ConnectorHTTP()

    def enqueue(
        self,
        user,
        *,
        channel,
        recipient,
        subject,
        body,
        request_id,
        connection_id=None,
        source_refs=None,
    ):
        AccessRepository(self.db).require_active(user)
        # Serialize idempotent creates for this actor. Never commit caller transactions.
        self.db.scalar(select(User).where(User.id == user.id).with_for_update())
        previous = self.db.scalar(
            select(Delivery).where(Delivery.owner_id == user.id, Delivery.request_id == request_id)
        )
        if previous:
            if any(
                getattr(previous, k) != v
                for k, v in {
                    "channel": channel,
                    "recipient": recipient,
                    "subject": subject,
                    "body": body,
                    "connection_id": connection_id,
                }.items()
            ):
                raise ServiceError(
                    "This request ID was already used for a different delivery.", 409
                )
            return previous
        if channel == "email":
            email_address(recipient)
            if not self.settings.smtp_host or not self.settings.smtp_from:
                raise ServiceError("Email delivery is not configured. Ask your administrator.", 503)
        elif channel in CHANNEL_PROVIDERS:
            connection = self.connections.repo.get(user, connection_id)
            if connection.provider != CHANNEL_PROVIDERS[channel]:
                raise ServiceError(f"Choose a {channel.title()} connection.", 422)
            self.connections.credentials(connection)
        else:
            raise ServiceError("Delivery channel must be email, Teams or Slack.", 422)
        if (
            "\n" in subject
            or "\r" in subject
            or len(subject) > 300
            or not body
            or len(body) > 100000
        ):
            raise ServiceError("Invalid delivery subject or body.", 422)
        refs = source_refs or []
        if not AccessRepository(self.db).source_refs_authorized(user, refs):
            raise ServiceError("A delivery source is no longer available.", 403)
        now = utcnow()
        obj = Delivery(
            owner_id=user.id,
            connection_id=connection_id,
            channel=channel,
            recipient=recipient,
            subject=subject,
            body=body,
            source_refs=refs,
            request_id=request_id,
            status="pending",
            undo_until=now + timedelta(seconds=20),
            available_at=now + timedelta(seconds=20),
            attempts=0,
        )
        self.db.add(obj)
        self.db.flush()
        return obj

    def cancel(self, user, rid):
        obj = self.db.scalar(
            select(Delivery)
            .where(Delivery.id == rid, Delivery.owner_id == user.id)
            .with_for_update()
        )
        if obj is None:
            raise NotFound("Delivery not found")
        if obj.status == "cancelled":
            return obj
        if obj.status != "pending":
            raise ServiceError("Dispatch has already started; recall cannot be guaranteed.", 409)
        obj.status = "cancelled"
        self.db.flush()
        return obj

    def _send_email(self, obj):
        message = EmailMessage()
        message["From"] = email_address(self.settings.smtp_from)
        message["To"] = email_address(obj.recipient)
        message["Subject"] = obj.subject
        # Stable Message-ID assists provider-side deduplication, without claiming a guarantee.
        message["Message-ID"] = f"<{obj.id}@voptimai.local>"
        message.set_content(obj.body)
        dispatch_started = False
        try:
            implicit_tls = (
                getattr(self.settings, "smtp_ssl", False) or self.settings.smtp_port == 465
            )
            client = (
                smtplib.SMTP_SSL(
                    self.settings.smtp_host,
                    self.settings.smtp_port,
                    timeout=30,
                    context=ssl.create_default_context(),
                )
                if implicit_tls
                else smtplib.SMTP(self.settings.smtp_host, self.settings.smtp_port, timeout=30)
            )
            with client as smtp:
                smtp.ehlo()
                if self.settings.smtp_starttls and not implicit_tls:
                    smtp.starttls(context=ssl.create_default_context())
                    smtp.ehlo()
                if self.settings.smtp_username:
                    smtp.login(self.settings.smtp_username, self.settings.smtp_password)
                dispatch_started = True
                smtp.send_message(message)
        except (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
        ):
            raise
        except (smtplib.SMTPException, OSError) as exc:
            # Before SMTP DATA no message can have been delivered. An explicit
            # temporary DATA rejection is likewise safe to retry.
            safe = (
                not dispatch_started
                or isinstance(exc, smtplib.SMTPDataError)
                and 400 <= exc.smtp_code < 500
            )
            if safe:
                failure = ProviderError(
                    "SMTP connection failed before acceptance. Delivery will retry.", retryable=True
                )
                failure.safe_to_retry = True
                raise failure from exc
            raise

    async def tick(self):
        """One due message per tick. Claim commit occurs before network I/O."""
        now = utcnow()
        # A crash after claim may have delivered externally; never resend automatically.
        stale = self.db.scalars(
            select(Delivery)
            .where(Delivery.status == "sending", Delivery.updated_at < now - timedelta(minutes=5))
            .limit(20)
            .with_for_update(skip_locked=True)
        ).all()
        for item in stale:
            item.status = "unknown"
            item.last_error = (
                "Worker stopped during dispatch. Check the destination before resending."
            )
        obj = self.db.scalar(
            select(Delivery)
            .where(
                Delivery.status == "pending",
                Delivery.available_at <= now,
                Delivery.undo_until <= now,
            )
            .order_by(Delivery.available_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if obj is None:
            self.db.commit()
            return bool(stale)
        user = self.db.get(User, obj.owner_id)
        try:
            if (
                not user
                or not user.active
                or not AccessRepository(self.db).source_refs_authorized(user, obj.source_refs)
            ):
                raise ServiceError("Delivery owner or source access is no longer valid.", 403)
            webhook = None
            if obj.channel == "teams":
                connection = self.connections.repo.get(user, obj.connection_id)
                webhook = validate_url(
                    self.connections.credentials(connection)["webhook_url"],
                    allowed_hosts=("logic.azure.com", "api.powerplatform.com"),
                )
            elif obj.channel == "slack":
                connection = self.connections.repo.get(user, obj.connection_id)
                webhook = slack_webhook(self.connections.credentials(connection)["webhook_url"])
            obj.status = "sending"
            obj.attempts += 1
            self.db.commit()
            if obj.channel == "email":
                await asyncio.to_thread(self._send_email, obj)
            elif obj.channel == "slack":
                await self.http.request(
                    "POST",
                    webhook,
                    json={
                        "text": obj.subject,
                        "blocks": [
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": f"*{obj.subject}*"},
                            },
                            {
                                "type": "section",
                                "text": {"type": "mrkdwn", "text": obj.body[:2900]},
                            },
                        ],
                    },
                )
            else:
                await self.http.request(
                    "POST",
                    webhook,
                    json={
                        "type": "message",
                        "attachments": [
                            {
                                "contentType": "application/vnd.microsoft.card.adaptive",
                                "contentUrl": None,
                                "content": {
                                    "type": "AdaptiveCard",
                                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                                    "version": "1.4",
                                    "body": [
                                        {
                                            "type": "TextBlock",
                                            "text": obj.subject,
                                            "weight": "Bolder",
                                            "wrap": True,
                                        },
                                        {"type": "TextBlock", "text": obj.body, "wrap": True},
                                    ],
                                },
                            }
                        ],
                    },
                )
            obj.status = "sent"
            obj.sent_at = utcnow()
            obj.last_error = None
            if obj.channel in CHANNEL_PROVIDERS:
                connection.status = "connected"
                connection.last_error = None
        except ProviderError as exc:
            # HTTP 429 is an explicit rejection. Other failures can be ambiguous.
            if (
                getattr(exc, "safe_to_retry", False) or "HTTP 429" in exc.detail
            ) and obj.attempts < 4:
                obj.status = "pending"
                obj.available_at = utcnow() + timedelta(seconds=30 * 2**obj.attempts)
            else:
                obj.status = (
                    "failed"
                    if getattr(exc, "safe_to_retry", False)
                    or "HTTP 429" in exc.detail
                    or obj.status != "sending"
                    else "unknown"
                )
            obj.last_error = exc.detail[:500]
        except (
            smtplib.SMTPAuthenticationError,
            smtplib.SMTPRecipientsRefused,
            smtplib.SMTPSenderRefused,
        ) as exc:
            obj.status = "failed"
            obj.last_error = (
                "SMTP rejected authentication, sender or recipient. Check configuration."
            )
        except (smtplib.SMTPException, OSError) as exc:
            obj.status = "unknown"
            obj.last_error = (
                "SMTP dispatch outcome is uncertain. Check the destination before resending."
            )
        except ServiceError as exc:
            obj.status = "failed"
            obj.last_error = exc.detail[:500]
        except (NotFound, AuthorizationError):
            obj.status = "failed"
            obj.last_error = "The delivery connection is no longer accessible."
        self.db.commit()
        return True
