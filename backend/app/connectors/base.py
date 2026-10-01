"""Bounded HTTP transport and encrypted-at-rest connector credentials."""

import ipaddress
import json
import socket
from urllib.parse import urlsplit
import httpx
from cryptography.fernet import Fernet, InvalidToken
from app.services.errors import ServiceError, ProviderError


class CredentialVault:
    def __init__(self, key: str):
        if not key:
            raise ServiceError(
                "Configure CREDENTIAL_ENCRYPTION_KEY before adding connections.", 503
            )
        try:
            self.cipher = Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise ServiceError("CREDENTIAL_ENCRYPTION_KEY must be a Fernet key.", 503) from exc

    def encrypt(self, value: dict) -> str:
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value: str) -> dict:
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ServiceError(
                "Connection credentials cannot be decrypted. Restore the installation key or reconnect.",
                503,
            ) from exc


def validate_url(
    url: str, *, allow_private=False, allowed_hosts: tuple[str, ...] = (), allow_query=True
) -> str:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ({"http", "https"} if allow_private else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ServiceError("Use an HTTPS endpoint without embedded credentials.", 422)
    if parsed.query and not allow_query:
        raise ServiceError("This endpoint must not include query parameters.", 422)
    host = parsed.hostname.lower()
    if allowed_hosts and not any(
        host == domain or host.endswith("." + domain) for domain in allowed_hosts
    ):
        raise ServiceError("The endpoint is outside the provider allowlist.", 422)
    if not allow_private:
        try:
            addresses = {
                item[4][0]
                for item in socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
            }
        except (OSError, ValueError) as exc:
            raise ServiceError("The connector hostname could not be resolved.", 422) from exc
        if any(not ipaddress.ip_address(ip).is_global for ip in addresses):
            raise ServiceError(
                "Private endpoints require explicit administrator configuration.", 422
            )
    return url.rstrip("/")


class ConnectorHTTP:
    def __init__(self, transport=None, timeout=30):
        self.transport, self.timeout = transport, timeout

    async def request(self, method, url, *, max_bytes=2_000_000, **kwargs):
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                timeout=self.timeout,
                follow_redirects=False,
                trust_env=False,
            ) as client:
                async with client.stream(method, url, **kwargs) as response:
                    if not 200 <= response.status_code < 300:
                        raise ProviderError(
                            f"Provider returned HTTP {response.status_code}. Check permissions and connection settings.",
                            retryable=response.status_code == 429 or response.status_code >= 500,
                        )
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > max_bytes:
                            raise ProviderError(
                                "Provider response exceeds the configured size limit."
                            )
                    return bytes(content)
        except httpx.TimeoutException as exc:
            raise ProviderError("Provider request timed out.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise ProviderError("Provider connection failed.", retryable=True) from exc

    async def json(self, method, url, **kwargs):
        try:
            return json.loads(await self.request(method, url, **kwargs))
        except (ValueError, TypeError) as exc:
            raise ProviderError("Provider returned malformed JSON.") from exc
