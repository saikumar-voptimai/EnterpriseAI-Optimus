# Using V-OptimAIse

V-OptimAIse has a personal assistant and governed shared workspaces. A private note is not automatically a team publication; a management briefing uses sources the recipient may see. Administrators configure the enterprise, while workspace managers approve team context and analytical schedules.

## First administrator and enterprise setup

Run `scripts/start.sh --tailscale` on the Jetson, then open the printed address while connected to Tailscale. Enter the setup token from the terminal and choose the administrator's name, email, password and enterprise name. Setup creates the enterprise scope and an initial coordination workspace. No default password or fabricated operational records are installed.

In administration, create the site's scope tree and employees. Use generic hierarchy labels such as enterprise, site, unit and team. Add unit workspaces and their members. Give at least one accountable senior a manager role. Configure read-only data connections and upload the team's documents before publishing a complete Workspace Brief.

A scope locates a workspace in the organization. A workspace contains collaboration and context. A user's personal projects organize their own notes. A Meeting Room is tied to a meeting and its participants; it is not a new organizational unit.

## Workspace Brief

Start with a short Purpose when creating the workspace. Open its brief editor to add responsibilities, asset/tag terminology, units, shift timezone, preferred output structure, procedures and escalation context. You can ask the assistant to draft the brief using permitted workspace inputs, then inspect and publish a revision.

Example:

> This workspace supports the utilities maintenance team. Use SI units and Asia/Kolkata time. Explain observations with the relevant readings and source times, likely causes, recommended checks and missing information. Prefer the current approved SOP. Distinguish measured values from hypotheses.

Published briefs are loaded for new runs. Drafting another revision does not silently replace the published one. The brief cannot grant data access, enable an unapproved tool, or authorize a machine command. The older Purpose is a fallback until a published brief exists.

## Chat and transparent context

Choose personal chat or a workspace conversation, enter a question and permit external AI processing where requested. The assistant can select relevant curated skills, search permitted knowledge, inspect workspace incidents/reports/statistics and read configured data through its allowed tools. A run has a visible status, bounded tool activity and cancellation.

Use context preview and source references to understand what informed a result. Personal instructions affect personal conversations; shared answers use only the appropriate preferences and shared sources. If evidence changes or access is revoked during execution, the runtime rechecks it before storing the answer. Ask a specific follow-up when data is missing rather than treating a plausible explanation as a measurement.

In personal chat, an explicit request can save a note or create a reminder through the action journal. A reminder needs an unambiguous date and time; uncertain wording produces a clarification. These personal write tools are unavailable to shared workspace jobs.

The agent does not execute arbitrary Python, arbitrary SQL/Flux, or PLC write commands. A deterministic calculation is distinct from model interpretation. The runtime uses LangGraph for the bounded tool loop; you do not need to author a graph to ask a question.

## Knowledge documents

Upload supported text, Markdown, CSV, JSON, text-based PDF, or DOCX files to the intended personal project or workspace. Originals are retained with new document revisions. Extracted content is split into overlapping token chunks; lexical search becomes available while approved embedding work runs in the background.

Inspect the document's indexing status. A remote embedding failure does not mean the original has disappeared. Retry indexing after fixing credentials/provider configuration. Upload a new revision to replace content without losing its version history. Earlier 1.0 documents have extracted text only; their original files cannot be reconstructed and must be reuploaded if originals are needed.

Image-only scans and handwriting require a separate OCR step; this release does not claim OCR support. Respect the configured upload size shown by the application.

## Personal capture: thoughts, observations and commitments

Capture text or use the microphone in an active HTTPS browser session. Voice uses the configured transcription provider; it is not always listening. Read the transcription before accepting a consequential interpretation.

The original note is saved first. The fast classification step may identify an observation, thought, remembered fact, task or reminder, and suggest its project/folder. One capture may create several linked items. If classification fails, the note remains available and can be retried.

For example, “I will analyse today's furnace data more deeply” expresses a commitment but not a specified deadline. Optimus can propose a follow-up using your preferences and synced calendar. Review the suggested time; the underlying phrase “today's data” must still refer to the capture date.

Configure personal behavior as off, suggest or automatic where offered. Reminder and memory proposals can be reviewed before applying. Internal changes are recorded in Activity and support conflict-aware Undo. A 20-second popup is a convenience; the action history remains after it disappears. Undo may be blocked if a later edit would be overwritten.

## Daily check-ins and weekly organization

Enable a daily check-in and set its local time. The scheduler creates an in-app prompt; opening it starts a short reflection session. Capture what happened, unresolved questions and next steps, then complete the check-in. This is not an unsolicited telephone call.

The weekly organizer proposes folders/placements using your actual captures. In automatic mode it can apply the supported filing changes; original notes and linked commitments remain separate from folder placement. Review the organization history and reverse a change set if it is unhelpful.

Set your canonical timezone, language, units and response style in Preferences. A personal routine's local schedule and calendar availability depend on that timezone and current sync state.

## Data connections

For the initial plant connection, mirror permitted SCADA/PLC signals into **InfluxDB v2** or use an existing read-only plant gateway feeding it. Configure the endpoint, organization and read-only token, discover buckets, then select measurement, field, tags and time window. Record what the tag means and its units in the Workspace Brief.

The connector generates bounded read queries. It does not establish a hardware one-way link and does not implement direct PLC, Modbus, OPC UA or historian protocols in this release. Restrict the token and network path at the plant boundary.

## Scheduled jobs and approvals

Employees can draft an analytical job for a workspace. Specify the instructions or deterministic report kind, selected source/metric, schedule, evaluation window and delivery preferences. “Draft with AI” turns your instructions into a proposal; select real signals before applying it. Drafting never creates or approves a live schedule. Submit the revision for a manager's review. Approval applies to that revision, including its access and behavior; changing material settings requires review again.

The approved job runs without the user keeping the browser open. Inspect execution status, quality outcome, report evidence and errors. A completed AI report is not the same as a successful data-validation result. Dependencies can require acceptable results for the relevant evaluation window.

Suggested pilot sequence:

1. Configure an InfluxDB metric with its expected sampling interval, units and validation bounds.
2. Create a 15-minute validation job. Inspect `pass`, `warn`, `fail` or `unavailable` and its evidence.
3. Configure the eight-hour shift handover against the site's actual shift boundaries/timezone.
4. Make numerical analysis depend on appropriate validation, while permitting a handover to describe missing data where configured.
5. Configure morning management briefs and weekly comparison reporting with explicit dates and monthly baseline labels.

Known rules are calculated deterministically. The displayed 0–100 deviation score is a within-window statistical heuristic, not a calibrated failure probability. A probability requires a defined calibrated model; this release does not invent probabilities from an LLM description. Configure the approved alert rule with severity, outcomes and a consecutive-failure threshold. The shared incident service creates one open finding per matching issue; later failures append evidence. Publication to management and critical escalation require explicit approved settings. Resolving an incident closes that episode; a later failure may create a new one.

## Outlook calendar and Meeting Rooms

An administrator supplies the Microsoft application registration settings, then each person connects Outlook. Choose calendars and synchronize. The cache covers a bounded recent/upcoming window and refreshes in the connector process. A disconnected account cannot silently continue acquiring new calendar data.

Create a Meeting Room with a title, time and verified internal attendees. Attach supplied MoM, notes or transcript text. Alternatively import an accessible Zoom/Teams cloud artifact. Zoom requires an account-authorized server-to-server connection and an available completed transcript. Teams uses the Graph online-meeting identifier and transcript identifier; a calendar-event ID or invitation URL is not a substitute.

Start draft extraction and monitor its progress. Longer artifacts are processed in durable bounded chunks by the meeting worker; cancellation stops further extraction without deleting the source. Then review the summary, decisions, action owners, deadlines and evidence. Unknown owners/deadlines should remain unresolved. Select the destination workspaces, approve the reviewed revision and publish. Attendees can accept their personal update package. Archiving the room removes it from active collaboration without deleting approved minutes or accepted commitments. Rooms are automatically archived after their configured expiry (initially 30 days).

Meeting participants and workspace recipients are different audiences. Publishing is an explicit action under destination permissions. There is no recording bot, automatic attendance, general inbox ingestion, or Google Meet connector in this release; owner-supplied artifacts remain the fallback.

## Email, Teams and Undo

Email uses configured enterprise SMTP; Teams uses a Workflows webhook connection. Outgoing deliveries are durably queued with a 20-second cancellation period and their own status. Undo cancels only while the delivery is pending and the deadline permits it. Once dispatch has begun, the app cannot promise that a message can be recalled.

A timeout after a provider might have accepted a message is shown as unknown rather than automatically sending another copy. Inspect the destination before retrying such a delivery. Notification links require application login and, for this pilot, Tailscale access.

## Three-person demonstration

Use separate accounts for the engineer, plant head and executive so the permission and context differences are visible.

| Persona | Demonstration |
| --- | --- |
| Engineer | Capture a voice observation, review a reminder, inspect a validation run, prepare handover |
| Plant head | Review authorized unit information, approve an analytical schedule, inspect supporting incident/report evidence |
| Executive | Ask for a management briefing, compare production periods, review meeting context and commitments |

Use real permitted data or clearly labelled uploaded demonstration data. Do not portray a generated explanation as a measured anomaly, hide incomplete periods, or compare a weekly total to a monthly total without normalizing the period.

## If something fails

A missing API key, denied OAuth consent, unavailable transcript, stale source or provider outage should produce a visible unavailable/failed state. Preserve the original note/artifact, inspect the connection and run details, and retry only the appropriate stage. Ask your administrator to use `scripts/diagnose.sh` for service readiness. A hardware, network or tenant-specific connection is complete only after testing it in the actual pilot environment.
