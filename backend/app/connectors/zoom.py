"""Zoom account-scoped cloud recording transcript reads."""

from urllib.parse import quote
import httpx
from app.connectors.base import ConnectorHTTP, validate_url
from app.services.errors import ProviderError


class ZoomAdapter:
    def __init__(self, credentials, http=None):
        self.credentials, self.http = credentials, http or ConnectorHTTP()

    async def token(self):
        result = await self.http.json(
            "POST",
            "https://zoom.us/oauth/token",
            auth=httpx.BasicAuth(self.credentials["client_id"], self.credentials["client_secret"]),
            data={
                "grant_type": "account_credentials",
                "account_id": self.credentials["account_id"],
            },
        )
        if not result.get("access_token"):
            raise ProviderError("Zoom did not return an account access token.")
        return result["access_token"]

    async def recordings(self, meeting_id):
        token = await self.token()
        # UUIDs beginning with / or containing // require Zoom's double encoding.
        encoded = quote(meeting_id, safe="")
        if meeting_id.startswith("/") or "//" in meeting_id:
            encoded = quote(encoded, safe="")
        result = await self.http.json(
            "GET",
            "https://api.zoom.us/v2/meetings/" + encoded + "/recordings",
            headers={"Authorization": "Bearer " + token},
        )
        return result, token

    async def transcript(self, meeting_id, artifact_id):
        result, token = await self.recordings(meeting_id)
        artifact = next(
            (
                r
                for r in result.get("recording_files", [])
                if r.get("id") == artifact_id
                and r.get("recording_type") == "audio_transcript"
                and r.get("status") == "completed"
            ),
            None,
        )
        if not artifact:
            raise ProviderError("A completed transcript was not found for this meeting.")
        url = validate_url(artifact["download_url"], allowed_hosts=("zoom.us",))
        content = await self.http.request("GET", url, headers={"Authorization": "Bearer " + token})
        return content.decode("utf-8-sig")
