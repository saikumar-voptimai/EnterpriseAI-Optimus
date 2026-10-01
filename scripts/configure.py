#!/usr/bin/env python3
"""Create a safe local configuration without executing .env as shell code."""

from __future__ import annotations
import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / ".env"


def read_values(path: Path | None = None) -> dict[str, str]:
    path = ENV if path is None else path
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                value = value.strip()
                if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                values[key.strip()] = value
    return values


def save_values(updates: dict[str, str], *, create_only: bool = False) -> None:
    if ENV.is_symlink():
        raise SystemExit("Refusing to modify a symlinked .env.")
    for key, value in updates.items():
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or any(c in value for c in "\n\r\x00"):
            raise SystemExit("Invalid configuration value.")
    text = (ENV if ENV.exists() else ROOT / ".env.example").read_text()
    lines = text.splitlines()
    seen = set()
    for i, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key = line.split("=", 1)[0].strip()
        if key in updates:
            # Single quotes keep dollar signs literal under Docker Compose.
            value = updates[key]
            if "'" in value or "\\" in value:
                raise SystemExit(
                    f"{key} contains unsupported quoting characters; configure it manually."
                )
            lines[i] = key + "='" + value + "'"
            seen.add(key)
    lines.extend(key + "='" + value + "'" for key, value in updates.items() if key not in seen)
    content = "\n".join(lines) + "\n"
    if create_only:
        fd = os.open(ENV, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as target:
            target.write(content)
    else:
        fd, temporary = tempfile.mkstemp(prefix=".env-", dir=ROOT)
        try:
            with os.fdopen(fd, "w") as target:
                target.write(content)
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, ENV)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def tailscale_origin() -> str:
    try:
        status = json.loads(subprocess.check_output(["tailscale", "status", "--json"], text=True))
    except (OSError, subprocess.CalledProcessError, ValueError) as exc:
        raise SystemExit(
            "Tailscale must be installed, running, and signed in before --tailscale setup."
        ) from exc
    if status.get("BackendState") != "Running":
        raise SystemExit("Tailscale is not connected. Complete tailscale up first.")
    name = status.get("Self", {}).get("DNSName", "").rstrip(".")
    if not re.fullmatch(r"[a-zA-Z0-9.-]+\.ts\.net", name):
        raise SystemExit("Cannot determine the full Tailscale HTTPS hostname. Check MagicDNS.")
    return "https://" + name.lower()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--create-only", action="store_true")
    parser.add_argument("--mode", choices=["local", "tailscale"])
    parser.add_argument("--configure-ai", action="store_true")
    parser.add_argument("--no-ai", action="store_true")
    parser.add_argument("--show-setup", action="store_true")
    args = parser.parse_args()
    if args.show_setup:
        values = read_values()
        print("Open " + values.get("APP_ORIGIN", "http://localhost:8080"))
        print("First-run setup token: " + values.get("BOOTSTRAP_TOKEN", "(not configured)"))
        print("Enter this token in the first-run page; it stops working once setup is complete.")
        return
    if args.create_only and ENV.exists():
        raise SystemExit("Refusing to overwrite existing .env. Run start.sh to reuse it.")
    values = read_values()
    updates = {}
    for name, factory in (
        ("POSTGRES_PASSWORD", lambda: secrets.token_hex(32)),
        ("BOOTSTRAP_TOKEN", lambda: secrets.token_urlsafe(32)),
        (
            "CREDENTIAL_ENCRYPTION_KEY",
            lambda: base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        ),
    ):
        if not values.get(name):
            updates[name] = factory()
    mode = args.mode or ("local" if not ENV.exists() else None)
    if mode == "local":
        updates.update(
            APP_ORIGIN="http://localhost:" + values.get("APP_PORT", "8080"),
            SESSION_COOKIE_SECURE="false",
            COMPOSE_FILE="compose.yaml",
        )
    elif mode == "tailscale":
        updates.update(
            APP_ORIGIN=tailscale_origin(), SESSION_COOKIE_SECURE="true", COMPOSE_FILE="compose.yaml"
        )
    if args.no_ai:
        updates["ALLOW_EXTERNAL_AI"] = "false"
    elif args.configure_ai and not values.get("OPENROUTER_API_KEY"):
        key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not key:
            try:
                key = getpass.getpass("OpenRouter API key (blank starts without AI): ").strip()
            except (EOFError, KeyboardInterrupt):
                raise SystemExit(
                    "Set OPENROUTER_API_KEY in .env, or use --no-ai for a core-only installation."
                )
        updates.update(OPENROUTER_API_KEY=key, ALLOW_EXTERNAL_AI="true" if key else "false")
    save_values(updates, create_only=args.create_only)
    print(
        "Configuration created."
        if not values
        else "Configuration checked; existing secrets retained."
    )


if __name__ == "__main__":
    main()
