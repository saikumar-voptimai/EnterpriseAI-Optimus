# Setting up integrations

Each integration needs an app or credential that only the account owner can create.
Sign-in return addresses use the app's address, `http://localhost:8088` on the laptop;
the exact addresses are under *Administration → Integrations → Sign-in return addresses*.
After changing `.env`, restart the app (`Ctrl+C`, then `./scripts/local/start.sh`).

## Where each connection lives

| Place | Who uses it | What belongs there |
| --- | --- | --- |
| **My connections** (sidebar → Account) | Only you | Your Google account (calendar, Drive files, Meet transcripts), your Microsoft 365 account (Outlook, Teams transcripts), personal Slack/Teams alerts |
| **Workspace → Connections** | The workspace's members and its assistant | Data sources (process historian, SQL database, Google Drive folder) and the team's Slack/Teams alert channels |
| **Administration → Integrations** | Everyone in the organization | Company Zoom account; readiness of Google/Microsoft sign-in, email and voice |

A Teams "channel" connection posts through a Teams Workflow, which can target a channel
or a group chat; it sends alerts, it does not read conversations. Slack works the same way.

| Integration | What people get | Who sets it up | Where |
| --- | --- | --- | --- |
| Google Workspace | Calendar, Drive file import, Google Meet transcripts | Administrator once, then each person signs in | Google Cloud console + `.env` |
| Microsoft 365 | Outlook calendar, Teams meeting transcripts | Administrator once, then each person signs in | Microsoft Entra admin center + `.env` |
| Zoom | Recorded meeting transcripts in Meeting Rooms | Administrator | Zoom App Marketplace + Administration → Integrations |
| Google Drive folder | Agents read spreadsheets in a shared folder | Administrator | Google Cloud service account + Workspace → Connections |
| SQL database | Agents read chosen tables | Administrator | Read-only database user + Workspace → Connections |
| Slack / Teams | Notifications and alerts | Anyone managing the space | Slack app or Teams Workflows + Connections |
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

6. Restart, open **My connections** (sidebar → Account), choose **Google Workspace → Connect**,
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

## Zoom (company account, meeting transcripts)

1. <https://marketplace.zoom.us/> → **Develop → Build App → Server-to-Server OAuth**.
2. Scopes: permission to read the account's cloud recordings (for example
   `cloud_recording:read:list_recording_files:admin`), then **Activate**.
3. In Optimus: **Administration → Integrations → Zoom → Add**, and enter the Account ID,
   Client ID and Client secret. It is configured once for everyone.
4. Cloud recording with **audio transcript** must be enabled in Zoom settings. Transcripts
   are available after the meeting ends.

People can import transcripts only of meetings they hosted (matched by email address);
administrators can import any meeting's transcript.

## Google Drive shared folder (spreadsheets for agents)

The folder is read through a Google *service account*: a robot account you share the
folder with. Access belongs to the workspace rather than to one person's Google sign-in,
and it keeps working when people change roles.

1. In the Google Cloud project used for sign-in: **IAM & Admin → Service Accounts →
   Create service account** (for example *optimus-reader*). No roles are needed.
2. Open it → **Keys → Add key → Create new key → JSON**. A key file downloads; keep it safe.
3. Make sure **Google Drive API** is enabled for the project.
4. In Google Drive, open the folder → **Share** → add the service account's email
   (`optimus-reader@<project>.iam.gserviceaccount.com`) as **Viewer**.
5. In Optimus: open the workspace → **Connections → Google Drive folder → Add**, paste the
   folder link and the key file contents, then save. The folder's files are listed to
   confirm access.

Agents can then list the folder (including subfolders) and read Google Sheets, Excel
(`.xlsx`) and CSV files as tables, always the latest version. Ask for example: *"Using the
BF2 daily sheet in the shared folder, what was the average coke rate last week?"*

## SQL database (read-only tables for agents)

1. Create a database user that can only read what you want to share, for example:

   ```sql
   CREATE ROLE optimus_reader LOGIN PASSWORD 'choose-a-strong-password';
   GRANT CONNECT ON DATABASE plantdb TO optimus_reader;
   GRANT USAGE ON SCHEMA production TO optimus_reader;
   GRANT SELECT ON production.heats, production.downtime TO optimus_reader;
   ```

2. In Optimus: open the workspace → **Connections → SQL database → Add**: server, port,
   database, encryption, and the read-only user. Tick *on the plant or office network*
   only for private addresses.
3. Tick the tables the workspace may read.

Agents never write SQL themselves: they describe a table and request columns, filters,
ordering and aggregates, which Optimus turns into parameterized queries. Every query runs
in a read-only transaction with a 15-second limit, and only ticked tables are reachable.

## Meeting transcripts

Transcripts turn into structured minutes (summary, decisions, actions, owners) in a
**Meeting Room**:

1. **Meeting Rooms → New room**: title, time and attendees.
2. Bring in the transcript, in one of these ways:
   - **Google Meet**: choose your Google connection, enter the Meet code from the meeting
     link (`abc-mnop-xyz`), then **Find transcripts**. Needs transcription turned on in the
     meeting (a Google Workspace edition that includes it).
   - **Microsoft Teams**: choose your Microsoft connection (connected with *Include meeting
     transcripts*), enter the Teams meeting ID.
   - **Zoom**: choose the company Zoom, enter the Zoom meeting ID (you must have hosted it).
   - **Any other source**: paste the minutes, notes or transcript text directly.
3. **Draft minutes**, review and correct them, choose the workspaces to publish to, then
   **Publish**. Attendees accept the actions assigned to them.

## Slack (notifications)

1. <https://api.slack.com/apps> → **Create New App → From scratch** in your workspace.
2. **Incoming Webhooks** → turn on → **Add New Webhook to Workspace** → choose the channel.
3. Copy the webhook address (`https://hooks.slack.com/services/...`).
4. In Optimus: **My connections** (personal alerts) or a workspace's **Connections** (team alerts) → **Slack → Add**, paste it, then send a test from
   **Notification delivery**. Messages wait 20 seconds before sending, so you can cancel.

## Microsoft Teams (notifications)

In the Teams channel: **Workflows → Post to a channel when a webhook request is
received**, copy the address, then **My connections** or a workspace's **Connections** → **Microsoft Teams → Add**. Workflows can also post to a group chat.

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
