"""Google delegated reads (Calendar, Drive, Meet transcripts) using OAuth code + PKCE.

Read-only scopes; requests go only to fixed Google API hosts. Drive content is
downloaded only for files a user explicitly imports, within the upload limit.
"""

from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit
from app.connectors.base import ConnectorHTTP
from app.services.errors import ProviderError, ServiceError

AUTHORIZE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
CALENDAR = "https://www.googleapis.com/calendar/v3"
DRIVE = "https://www.googleapis.com/drive/v3"
MEET = "https://meet.googleapis.com/v2"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
API_HOSTS = {"www.googleapis.com", "meet.googleapis.com", "openidconnect.googleapis.com"}
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/meetings.space.readonly",
]
# Google-native files are exported to formats the document extractor reads.
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}


class GoogleAdapter:
    def __init__(self, settings, http=None):
        self.settings, self.http = settings, http or ConnectorHTTP(
            timeout=settings.connector_timeout_seconds
        )

    @property
    def redirect_uri(self):
        return self.settings.app_origin + "/api/connections/google/callback"

    def authorize_url(self, state, challenge):
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise ServiceError("Google sign-in is not configured for this installation.", 503)
        return (
            AUTHORIZE_URL
            + "?"
            + urlencode(
                dict(
                    client_id=self.settings.google_client_id,
                    redirect_uri=self.redirect_uri,
                    response_type="code",
                    scope=" ".join(SCOPES),
                    state=state,
                    code_challenge=challenge,
                    code_challenge_method="S256",
                    access_type="offline",
                    prompt="consent select_account",
                    include_granted_scopes="true",
                )
            )
        )

    async def token(self, *, code=None, verifier=None, refresh_token=None):
        data = dict(
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret,
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
        result = await self.http.json("POST", TOKEN_URL, data=data)
        if not isinstance(result.get("access_token"), str) or not result["access_token"]:
            raise ProviderError("Google did not return an access token.")
        return result

    def _url(self, url):
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in API_HOSTS
            or parsed.username
            or parsed.port not in (None, 443)
        ):
            raise ProviderError("Google returned an invalid endpoint.")
        return url

    async def get(self, url, token, **kwargs):
        return await self.http.json(
            "GET", self._url(url), headers={"Authorization": "Bearer " + token}, **kwargs
        )

    async def pages(self, url, token, params, key, limit=5000):
        result, cursors, params = [], set(), dict(params or {})
        for _ in range(100):
            page = await self.get(url, token, params=params)
            items = page.get(key, [])
            if not isinstance(items, list):
                raise ProviderError("Google returned an invalid resource list.")
            result.extend(items)
            if len(result) > limit:
                raise ProviderError("Google returned more items than the configured limit.")
            cursor = page.get("nextPageToken")
            if not cursor:
                return result
            if cursor in cursors:
                raise ProviderError("Google returned a repeated pagination cursor.")
            cursors.add(cursor)
            params["pageToken"] = cursor
        raise ProviderError("Google pagination exceeded its limit.")

    async def account(self, token):
        profile = await self.get(USERINFO, token)
        return profile.get("email")

    async def calendars(self, token):
        items = await self.pages(
            CALENDAR + "/users/me/calendarList", token, {"maxResults": 250}, "items", limit=250
        )
        return [
            {
                "id": c["id"],
                "name": c.get("summaryOverride") or c.get("summary") or c["id"],
                "primary": bool(c.get("primary")),
            }
            for c in items
            if c.get("id")
        ]

    async def events(self, token, calendar_id, start, end):
        return await self.pages(
            CALENDAR + "/calendars/" + quote(calendar_id, safe="") + "/events",
            token,
            {
                "timeMin": start.isoformat(),
                "timeMax": end.isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": 250,
                "showDeleted": "false",
            },
            "items",
        )

    async def drive_files(self, token, search="", limit=25):
        query = "trashed = false and mimeType != 'application/vnd.google-apps.folder'"
        if search:
            query += " and name contains '" + search.replace("\\", "\\\\").replace("'", "\\'") + "'"
        page = await self.get(
            DRIVE + "/files",
            token,
            params={
                "q": query,
                "pageSize": max(1, min(int(limit), 50)),
                "orderBy": "modifiedTime desc",
                "fields": "files(id,name,mimeType,modifiedTime,size)",
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            },
        )
        return [
            {
                "id": f["id"],
                "name": f.get("name", ""),
                "mime_type": f.get("mimeType", ""),
                "modified_at": f.get("modifiedTime"),
                "size": int(f["size"]) if str(f.get("size", "")).isdigit() else None,
            }
            for f in page.get("files", [])
        ]

    async def drive_download(self, token, file_id, max_bytes):
        meta = await self.get(
            DRIVE + "/files/" + quote(file_id, safe=""),
            token,
            params={"fields": "id,name,mimeType,size", "supportsAllDrives": "true"},
        )
        name, mime = meta.get("name") or "document", meta.get("mimeType", "")
        headers = {"Authorization": "Bearer " + token}
        if mime in EXPORTS:
            export, suffix = EXPORTS[mime]
            url = DRIVE + "/files/" + quote(file_id, safe="") + "/export"
            params = {"mimeType": export}
            if not name.lower().endswith(suffix):
                name += suffix
        elif mime.startswith("application/vnd.google-apps."):
            raise ServiceError("This Google file type cannot be imported.", 415)
        else:
            if str(meta.get("size", "0")).isdigit() and int(meta.get("size", "0")) > max_bytes:
                raise ServiceError("The file exceeds the configured upload size.", 413)
            url = DRIVE + "/files/" + quote(file_id, safe="")
            params = {"alt": "media", "supportsAllDrives": "true"}
        content = await self.http.request(
            "GET", self._url(url), headers=headers, params=params, max_bytes=max_bytes
        )
        return name, content

    async def meet_transcripts(self, token, meeting_code):
        code = meeting_code.strip()
        if not code or len(code) > 64 or '"' in code or "\\" in code:
            raise ServiceError("Enter the Google Meet code, for example abc-mnop-xyz.", 422)
        records = await self.pages(
            MEET + "/conferenceRecords",
            token,
            {"filter": f'space.meeting_code = "{code}"', "pageSize": 25},
            "conferenceRecords",
            limit=100,
        )
        transcripts = []
        for record in records:
            name = record.get("name", "")
            if not name.startswith("conferenceRecords/"):
                continue
            for item in await self.pages(
                MEET + "/" + name + "/transcripts", token, {"pageSize": 25}, "transcripts", 100
            ):
                if item.get("state") == "FILE_GENERATED" and item.get("docsDestination"):
                    transcripts.append(
                        {
                            "id": item["name"],
                            "kind": "transcript",
                            "created_at": item.get("startTime") or record.get("startTime"),
                        }
                    )
        return transcripts

    async def meet_transcript_text(self, token, transcript_name):
        if not transcript_name.startswith("conferenceRecords/") or "/transcripts/" not in (
            transcript_name
        ):
            raise ServiceError("Invalid Google Meet transcript reference.", 422)
        transcript = await self.get(MEET + "/" + transcript_name, token)
        document = (transcript.get("docsDestination") or {}).get("document")
        if not document:
            raise ServiceError("This transcript has no generated document yet.", 409)
        content = await self.http.request(
            "GET",
            self._url(DRIVE + "/files/" + quote(document, safe="") + "/export"),
            headers={"Authorization": "Bearer " + token},
            params={"mimeType": "text/plain"},
        )
        return content.decode("utf-8-sig")


def google_datetime(value):
    """Event start/end -> aware UTC datetime. All-day dates start at 00:00 UTC."""
    if not isinstance(value, dict):
        raise ProviderError("Calendar event has an invalid date.")
    if value.get("dateTime"):
        date = datetime.fromisoformat(value["dateTime"].replace("Z", "+00:00"))
        if date.tzinfo is None:
            raise ProviderError("Calendar event time has no timezone.")
        return date.astimezone(timezone.utc)
    if value.get("date"):
        return datetime.fromisoformat(value["date"]).replace(tzinfo=timezone.utc)
    raise ProviderError("Calendar event has an invalid date.")


def google_event_values(event):
    """Map a Google event onto the provider-neutral calendar cache fields."""
    entry_points = (event.get("conferenceData") or {}).get("entryPoints") or []
    join = event.get("hangoutLink") or next(
        (p.get("uri") for p in entry_points if p.get("entryPointType") == "video"), None
    )
    conference = (event.get("conferenceData") or {}).get("conferenceId")
    return dict(
        title=(event.get("summary") or "Untitled meeting")[:500],
        cancelled=event.get("status") == "cancelled",
        busy=event.get("transparency") != "transparent",
        details={
            "organizer": event.get("organizer"),
            "attendees": event.get("attendees", []),
            "online_meeting": {"join_url": join, "meeting_code": conference} if join else None,
            "web_link": event.get("htmlLink"),
            "provider_modified_at": event.get("updated"),
        },
    )
