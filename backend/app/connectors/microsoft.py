"""Microsoft Graph delegated calendar and transcript reads using OAuth code + PKCE."""

from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit
from app.connectors.base import ConnectorHTTP
from app.services.errors import ServiceError, ProviderError

GRAPH = "https://graph.microsoft.com/v1.0"
BASE_SCOPES = ["offline_access", "User.Read", "Calendars.Read"]
TRANSCRIPT_SCOPES = ["OnlineMeetings.Read", "OnlineMeetingTranscript.Read.All"]


class MicrosoftAdapter:
    def __init__(self, settings, http=None):
        self.settings, self.http = settings, http or ConnectorHTTP(
            timeout=settings.connector_timeout_seconds
        )

    @property
    def redirect_uri(self):
        return self.settings.app_origin + "/api/connections/microsoft/callback"

    @property
    def authority(self):
        tenant = self.settings.microsoft_tenant_id
        if not tenant or any(
            c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-."
            for c in tenant
        ):
            raise ServiceError("Microsoft tenant ID is invalid.", 503)
        return "https://login.microsoftonline.com/" + tenant + "/oauth2/v2.0"

    def authorize_url(self, state, challenge, transcripts=False):
        if not self.settings.microsoft_client_id or not self.settings.microsoft_client_secret:
            raise ServiceError("Configure Microsoft application client ID and secret first.", 503)
        scopes = BASE_SCOPES + (TRANSCRIPT_SCOPES if transcripts else [])
        return (
            self.authority
            + "/authorize?"
            + urlencode(
                dict(
                    client_id=self.settings.microsoft_client_id,
                    response_type="code",
                    redirect_uri=self.redirect_uri,
                    response_mode="query",
                    scope=" ".join(scopes),
                    state=state,
                    code_challenge=challenge,
                    code_challenge_method="S256",
                    prompt="select_account",
                )
            )
        )

    async def token(self, *, code=None, verifier=None, refresh_token=None):
        data = dict(
            client_id=self.settings.microsoft_client_id,
            client_secret=self.settings.microsoft_client_secret,
        )
        if refresh_token:
            data.update(grant_type="refresh_token", refresh_token=refresh_token)
        else:
            data.update(
                grant_type="authorization_code",
                code=code,
                redirect_uri=self.redirect_uri,
                code_verifier=verifier,
            )
        result = await self.http.json("POST", self.authority + "/token", data=data)
        if not isinstance(result.get("access_token"), str) or not result.get("access_token"):
            raise ProviderError("Microsoft did not return an access token.")
        return result

    async def get(self, path, token, **kwargs):
        url = path if path.startswith("https://") else GRAPH + path
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "graph.microsoft.com"
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise ProviderError("Microsoft returned an invalid continuation endpoint.")
        return await self.http.json(
            "GET",
            url,
            headers={"Authorization": "Bearer " + token, "Prefer": 'outlook.timezone="UTC"'},
            **kwargs,
        )

    async def pages(self, path, token, params=None, limit=5000):
        result, seen = [], set()
        for _ in range(100):
            if path in seen:
                raise ProviderError("Microsoft returned a repeated pagination cursor.")
            seen.add(path)
            page = await self.get(path, token, params=params)
            if not isinstance(page.get("value"), list):
                raise ProviderError("Microsoft returned an invalid resource list.")
            result.extend(page["value"])
            if len(result) > limit:
                raise ProviderError(
                    "Calendar sync exceeds the configured resource limit. Select fewer calendars."
                )
            path, params = page.get("@odata.nextLink"), None
            if not path:
                return result
        raise ProviderError("Calendar sync exceeded its pagination limit.")

    async def calendars(self, token):
        return await self.pages(
            "/me/calendars", token, {"$select": "id,name,isDefaultCalendar,canEdit"}, limit=100
        )

    async def events(self, token, calendar_id, start, end):
        return await self.pages(
            "/me/calendars/" + quote(calendar_id, safe="") + "/calendarView",
            token,
            {
                "startDateTime": start.isoformat(),
                "endDateTime": end.isoformat(),
                "$top": "200",
                "$select": "id,subject,start,end,isCancelled,showAs,organizer,attendees,onlineMeeting,webLink,lastModifiedDateTime",
            },
        )

    async def transcript(self, token, meeting_id, transcript_id):
        path = (
            "/me/onlineMeetings/"
            + quote(meeting_id, safe="")
            + "/transcripts/"
            + quote(transcript_id, safe="")
            + "/content"
        )
        content = await self.http.request(
            "GET", GRAPH + path, headers={"Authorization": "Bearer " + token, "Accept": "text/vtt"}
        )
        return content.decode("utf-8-sig")

    async def transcripts(self, token, meeting_id):
        return await self.pages(
            "/me/onlineMeetings/" + quote(meeting_id, safe="") + "/transcripts", token, limit=100
        )


def graph_datetime(value):
    if not isinstance(value, dict) or not value.get("dateTime"):
        raise ProviderError("Calendar event has an invalid date.")
    date = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
    if date.tzinfo is None:
        if value.get("timeZone") not in ("UTC", "Etc/UTC"):
            raise ProviderError("Calendar response did not honor the requested UTC timezone.")
        date = date.replace(tzinfo=timezone.utc)
    return date.astimezone(timezone.utc)
