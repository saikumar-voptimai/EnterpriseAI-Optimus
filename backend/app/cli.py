"""Administrative operations run on the host; no public self-registration."""

import argparse
import getpass
import os
import re
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .db import SessionLocal
from .models import User, AuditEvent
from .auth import hash_password


def main():
    parser = argparse.ArgumentParser(description="V-OptimAIse local administrator tools")
    sub = parser.add_subparsers(dest="command", required=True)
    bootstrap = sub.add_parser("bootstrap", help="Create an administrator after running migrations")
    bootstrap.add_argument("--email")
    bootstrap.add_argument("--name")
    reset = sub.add_parser("reset-password", help="Reset a user password and revoke their sessions")
    reset.add_argument("--email", required=True)
    args = parser.parse_args()
    email = (args.email or input("Administrator email: ")).strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        parser.error("Enter a valid email address")
    password = os.environ.get("BOOTSTRAP_PASSWORD")
    if not password:
        password = getpass.getpass("Password (12+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            parser.error("Passwords did not match")
    try:
        hashed = hash_password(password)
    except ValueError as exc:
        parser.error(str(exc))
    with SessionLocal() as db:
        existing = db.scalar(select(User).where(User.email == email))
        if args.command == "bootstrap":
            if existing:
                parser.error("This account already exists; use reset-password if needed")
            name = (args.name or input("Administrator name: ")).strip()
            if not name or len(name) > 160:
                parser.error("Enter a name of 1–160 characters")
            user = User(
                email=email,
                name=name,
                password_hash=hashed,
                is_admin=True,
                active=True,
                clearance=3,
            )
            db.add(user)
            db.flush()
            db.add(
                AuditEvent(
                    actor_id=user.id,
                    action="administrator_bootstrapped",
                    resource_type="user",
                    resource_id=user.id,
                    details={},
                )
            )
        else:
            if not existing:
                parser.error("Account not found")
            from sqlalchemy import delete
            from .models import AuthSession

            existing.password_hash = hashed
            db.execute(delete(AuthSession).where(AuthSession.user_id == existing.id))
            db.add(
                AuditEvent(
                    actor_id=None,
                    action="password_reset_on_host",
                    resource_type="user",
                    resource_id=existing.id,
                    details={},
                )
            )
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            parser.error("Account changed concurrently; retry after reviewing the current account")
    print(
        "Administrator created."
        if args.command == "bootstrap"
        else "Password reset; existing sessions revoked."
    )


if __name__ == "__main__":
    main()
