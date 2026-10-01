"""Bound incoming bodies before multipart parsing or JSON materialization."""

from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse
from .config import get_settings


class RequestSizeLimit:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        # Voice captures have a separately bounded five-minute upload allowance.
        audio = scope.get("path") == "/api/speech/transcribe"
        limit = (25 * 1024 * 1024 if audio else get_settings().max_upload_bytes) + 65536
        headers = dict(scope.get("headers", []))
        try:
            declared = int(headers.get(b"content-length", b"0"))
        except ValueError:
            declared = -1
        if declared < 0 or declared > limit:
            response = JSONResponse(
                {"detail": "Request body exceeds the upload limit"}, status_code=413
            )
            return await response(scope, receive, send)
        consumed = 0

        async def bounded_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > limit:
                    raise HTTPException(413, "Request body exceeds the upload limit")
            return message

        await self.app(scope, bounded_receive, send)
