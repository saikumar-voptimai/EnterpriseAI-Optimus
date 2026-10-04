# Setting up integrations

Each integration needs an app or credential that only the account owner can create.
Every step below is done once per installation. Sign-in return addresses use the
app's address, `http://localhost:8088` on the laptop. Use the exact address shown
under *Connections → Readiness → Administrator setup*.

After changing `.env`, restart the app (`Ctrl+C`, then `./scripts/local/start.sh`).

| Integration | What people get | Who sets it up | Where |
| --- | --- | --- | --- |
| Google Workspace | Calendar, Drive file import, Google Meet transcripts | Administrator once, then each person signs in | Google Cloud console + `.env` |
| Microsoft 365 | Outlook calendar, Teams meeting transcripts | Administrator once, then each person signs in | Microsoft Entra admin center + `.env` |
| Zoom | Recorded meeting transcripts in Meeting Rooms | Administrator | Zoom App Marketplace + Connections page |
| Slack | Notifications and alerts in a channel | Anyone managing the space | Slack app settings + Connections page |
| Microsoft Teams | Notifications and alerts in a channel | Anyone managing the space | Teams Workflows + Connections page |
| Email | Notifications by email | Administrator | `.env` (done for Hostinger) |

## Google Workspace (Calendar, Drive, Meet)

1. Open <https://console.cloud.google.com/>, create a project (for example *Optimus*).
2. **APIs & Services → Library**: enable **Google Calendar API**, **Google Drive API**
   and **Google Meet REST API**.
3. **APIs & Services → OAuth consent screen** (Google Auth Platform):
   - User type **External** (or **Internal** if every user is in your Google Workspace).
   - App name *Optimus*, your support email.
   - **Data access → Add scopes**: `openid`, `.../auth/userinfo.email`,
     `.../auth/calendar.readonly`, `.../auth/drive.readonly`,
     `.../auth/meetings.space.readonly`.
   - **Audience → Test users**: add every Google account that will test.
     While the app is in *Testing*, only these accounts can sign in, and Google asks
     them to sign in again every 7 days.
4. **Clients → Create client → Web application**:
   - Authorized redirect URI: `http://localhost:8088/api/connections/google/callback`
5. Copy the client ID and secret into `.env`:

   ```
   GOOGLE_CLIENT_ID=...apps.googleusercontent.com
   GOOGLE_CLIENT_SECRET=...
   ```

   For a separate Google app per environment, suffix the keys with the environment
   name instead (`GOOGLE_CLIENT_ID_DEMO`, `GOOGLE_CLIENT_SECRET_DEMO`); with `APP_ENV=demo`
   (or `--env demo`) the start script applies them as `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`.
   The startup line `Environment: demo (GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET)` confirms it.

6. Restart, open **Connections** in your personal space, choose **Connect Google**,
   then open **Settings** on the connection to choose calendars, and **Drive files** to import documents.

Google Meet transcripts exist only when transcription was turned on in the meeting. That
needs a Google Workspace edition that includes it; personal Gmail accounts do not
produce transcripts. In a Meeting Room, choose the Google connection, enter the Meet code
from the link (`abc-mnop-xyz`), then choose **Find transcripts**.

## Microsoft 365 (Outlook calendar, Teams transcripts)

1. Open <https://entra.microsoft.com/> → **App registrations → New registration**.
   - Supported accounts: your organization only, or any organization for testing with others.
   - Redirect URI: **Web**, `http://localhost:8088/api/connections/microsoft/callback`
2. **Certificates & secrets → New client secret**; copy the value immediately.
3. **API permissions → Microsoft Graph → Delegated**: `User.Read`, `Calendars.Read`,
   `offline_access`; for transcripts also `OnlineMeetings.Read` and
   `OnlineMeetingTranscript.Read.All`, then **Grant admin consent**.
4. `.env`:

   ```
   MICROSOFT_CLIENT_ID=<Application (client) ID>
   MICROSOFT_CLIENT_SECRET=<secret value>
   MICROSOFT_TENANT_ID=<Directory (tenant) ID, or "organizations">
   ```

5. Restart, then **Connections → Connect Microsoft** (tick *Include meeting transcripts* if needed).

## Zoom (meeting transcripts)

1. <https://marketplace.zoom.us/> → **Develop → Build App → Server-to-Server OAuth**.
2. Scopes: permission to read the account's cloud recordings (for example
   `cloud_recording:read:list_recording_files:admin`), then **Activate**.
3. In Optimus: **Connections → Configure Zoom**, and enter the Account ID, Client ID and Client secret.
4. Cloud recording with **audio transcript** must be enabled in Zoom settings. Transcripts
   are available after the meeting ends. In a Meeting Room, enter the Zoom meeting ID.

## Slack (notifications)

1. <https://api.slack.com/apps> → **Create New App → From scratch** in your workspace.
2. **Incoming Webhooks** → turn on → **Add New Webhook to Workspace** → choose the channel.
3. Copy the webhook address (`https://hooks.slack.com/services/...`).
4. In Optimus: **Connections → Add Slack channel**, paste it, then send a test from
   **Notification delivery**. Messages wait 20 seconds before sending, so you can cancel.

## Microsoft Teams (notifications)

In the Teams channel: **Workflows → Post to a channel when a webhook request is
received**, copy the address, then **Connections → Add Teams channel**.

## Email (Hostinger)

Already configured in `.env` for Hostinger (`smtp.hostinger.com`, port 465, implicit TLS):

```
SMTP_HOST=smtp.hostinger.com
SMTP_PORT=465
SMTP_SSL=true
SMTP_STARTTLS=false
SMTP_USERNAME=<full mailbox address>
SMTP_PASSWORD=<mailbox password>
SMTP_FROM=<the same mailbox address>
```

Other providers: port 587 with `SMTP_STARTTLS=true` and `SMTP_SSL=false`.
