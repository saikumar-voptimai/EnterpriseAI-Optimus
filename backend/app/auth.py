"""Opaque server sessions, Argon2id passwords, Origin + CSRF protection."""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from secrets import token_urlsafe, compare_digest
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from .config import get_settings
from .db import get_session
from .models import AuthSession, User, LoginAttempt

COOKIE_NAME = "voptimai_session"
hasher = PasswordHasher(time_cost=2, memory_cost=32768, parallelism=2)
_dummy_hash = hasher.hash(token_urlsafe(32))


def utcnow():
    return datetime.now(timezone.utc)


def hash_password(value):
    if not 12 <= len(value) <= 256:
        raise ValueError("Passwords must contain 12–256 characters")
    return hasher.hash(value)


def verify_password(value, stored):
    try:
        return hasher.verify(stored or _dummy_hash, value)
    except (VerificationError, InvalidHashError):
        return False


def token_hash(value):
    return sha256(value.encode()).hexdigest()


def require_origin(request):
    if request.headers.get("origin", "").rstrip("/") != get_settings().app_origin:
        raise HTTPException(403, "Request origin is not allowed")


def rate_limit_login(db, email, client_ip):
    """Durable, locked counters. A failed login consumes quota; never logs a password."""
    now = utcnow()
    db.execute(delete(LoginAttempt).where(LoginAttempt.window_start < now - timedelta(days=1)))
    denied = False
    for value, limit in [("email:" + email, 10), ("ip:" + client_ip, 60)]:
        key = token_hash(value)
        db.execute(
            insert(LoginAttempt)
            .values(key=key, attempts=0, window_start=now)
            .on_conflict_do_nothing(index_elements=["key"])
        )
        attempt = db.scalar(select(LoginAttempt).where(LoginAttempt.key == key).with_for_update())
        if attempt.window_start < now - timedelta(minutes=15):
            attempt.attempts = 0
            attempt.window_start = now
        attempt.attempts += 1
        denied = denied or attempt.attempts > limit
    db.commit()
    if denied:
        raise HTTPException(
            429,
            "Too many sign-in attempts. Try again in 15 minutes.",
            headers={"Retry-After": "900"},
        )


@dataclass
class Actor:
    user: User
    session: AuthSession
    db: Session


def current_actor(request: Request, db: Session = Depends(get_session)):
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(401, "Sign in to continue")
    auth_session = db.scalar(
        select(AuthSession).where(
            AuthSession.token_hash == token_hash(token), AuthSession.expires_at > utcnow()
        )
    )
    user = db.get(User, auth_session.user_id) if auth_session else None
    if not user or not user.active or user.is_service:
        raise HTTPException(401, "Your session has expired")
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        require_origin(request)
        if not compare_digest(request.headers.get("x-csrf-token", ""), auth_session.csrf_token):
            raise HTTPException(403, "Session verification failed. Refresh and try again.")
    return Actor(user, auth_session, db)


def admin_actor(actor: Actor = Depends(current_actor)):
    if not actor.user.is_admin:
        raise HTTPException(403, "Administrator access required")
    return actor


def new_session(db, user):
    token = token_urlsafe(48)
    row = AuthSession(
        user_id=user.id,
        token_hash=token_hash(token),
        csrf_token=token_urlsafe(32),
        expires_at=utcnow() + timedelta(hours=get_settings().session_ttl_hours),
    )
    db.execute(delete(AuthSession).where(AuthSession.expires_at < utcnow()))
    # Bound sessions without a browser-state cache or reusable bearer token in JS.
    old = list(
        db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == user.id)
            .order_by(AuthSession.created_at.desc())
        )
    )
    for session in old[19:]:
        db.delete(session)
    db.add(row)
    db.flush()
    return row, token
