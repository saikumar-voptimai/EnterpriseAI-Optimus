"""Transactional commands for durable agent runs; graph execution lives in agents.runtime."""

import uuid
from datetime import timedelta
from sqlalchemy import select
from app.config import get_settings
from app.models import Conversation, Message, User, Workspace, utcnow
from app.models_agent import AgentRun, AgentRunEvent
from app.repositories import AccessRepository, NotFound
from app.services.context import ContextService
from app.services.errors import ServiceError
from app.services.gateway import select_model


class AgentRunService:
    def __init__(self, session, settings=None):
        self.session, self.settings = session, settings or get_settings()
        self.access = AccessRepository(session)

    def authorize_conversation(self, user, conversation_id):
        conversation = self.access.conversation(user, conversation_id, roles={"member", "manager"})
        if not self.settings.allow_external_ai:
            raise ServiceError("External AI is disabled for this installation.", 403)
        if conversation.workspace_id:
            workspace = self.access.workspace(
                user, conversation.workspace_id, roles={"member", "manager"}
            )
            if not workspace.external_ai_enabled:
                raise ServiceError("External AI is disabled for this workspace.", 403)
        return conversation

    def enqueue_chat(self, user, conversation_id, query, model=None, request_key=None):
        if not query.strip() or len(query) > 12000:
            raise ServiceError("A message must contain 1–12000 characters.")
        request_key = request_key or str(uuid.uuid4())
        if len(request_key) > 128:
            raise ServiceError("Request identifier is too long.")
        # Serialize idempotency decisions per principal across different conversations.
        self.session.scalar(select(User).where(User.id == user.id).with_for_update())
        prior = self.session.scalar(
            select(AgentRun).where(
                AgentRun.owner_id == user.id, AgentRun.request_key == request_key
            )
        )
        if prior:
            self.get(user, prior.id)
            message = self.session.get(Message, prior.user_message_id)
            if (
                prior.conversation_id != conversation_id
                or not message
                or message.content != query
                or (model and prior.model != model)
            ):
                raise ServiceError(
                    "This request identifier was already used for a different message.", 409
                )
            return prior
        conversation = self.authorize_conversation(user, conversation_id)
        self.session.scalar(
            select(Conversation).where(Conversation.id == conversation.id).with_for_update()
        )
        if self.session.scalar(
            select(AgentRun.id).where(
                AgentRun.conversation_id == conversation.id,
                AgentRun.status.in_(["queued", "running"]),
            )
        ):
            raise ServiceError(
                "An assistant response is already in progress in this conversation.", 409
            )
        selected_model = select_model(model, self.settings)
        # Validate the input budget before persisting work, not after billing begins.
        ContextService(self.session, self.settings).for_conversation(
            user, conversation, query=query
        )
        message = Message(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            author_id=user.id,
            role="user",
            content=query,
            usage={},
            source_refs=[],
        )
        self.session.add(message)
        self.session.flush()
        run = AgentRun(
            id=str(uuid.uuid4()),
            owner_id=user.id,
            conversation_id=conversation.id,
            workspace_id=conversation.workspace_id,
            project_id=conversation.project_id,
            user_message_id=message.id,
            request_key=request_key,
            model=selected_model,
            status="queued",
            stage="queued",
            source_refs=[],
            context_metadata={},
            usage={},
        )
        self.session.add(run)
        self.session.flush()
        self.session.add(AgentRunEvent(run_id=run.id, stage="queued", detail={}))
        conversation.updated_at = utcnow()
        return run

    def get(self, user, run_id):
        run = self.session.scalar(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.owner_id == user.id)
        )
        if run is None:
            raise NotFound("Agent run not found")
        if run.conversation_id:
            self.access.conversation(user, run.conversation_id)
        elif run.workspace_id:
            self.access.workspace(user, run.workspace_id)
        if run.source_refs and not self.access.source_refs_authorized(user, run.source_refs):
            raise NotFound("Agent run sources are no longer accessible")
        return run

    def cancel(self, user, run_id):
        run = self.get(user, run_id)
        run = self.session.scalar(select(AgentRun).where(AgentRun.id == run.id).with_for_update())
        if run.status in {"queued", "running"}:
            run.status, run.stage = "cancelled", "cancelled"
            run.lease_token = run.lease_until = None
            run.finished_at = utcnow()
            self.session.add(AgentRunEvent(run_id=run.id, stage="cancelled", detail={}))
        return run

    def serialize(self, user, run, include_events=True):
        self.get(user, run.id)
        keys = (
            "id",
            "conversation_id",
            "user_message_id",
            "assistant_message_id",
            "kind",
            "model",
            "status",
            "stage",
            "attempts",
            "created_at",
            "started_at",
            "finished_at",
            "error",
            "result",
            "source_refs",
            "context_metadata",
            "usage",
        )
        output = {key: getattr(run, key) for key in keys}
        if include_events:
            events = self.session.scalars(
                select(AgentRunEvent)
                .where(AgentRunEvent.run_id == run.id)
                .order_by(AgentRunEvent.created_at, AgentRunEvent.id)
                .limit(100)
            ).all()
            output["events"] = [
                {"stage": e.stage, "detail": e.detail, "created_at": e.created_at} for e in events
            ]
        return output
