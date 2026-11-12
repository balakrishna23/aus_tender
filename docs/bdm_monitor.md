# Illoura BuyICT alerts and BDM results

## Working view upgrade — 10 November 2026

Open `http://127.0.0.1:8767` on this computer. The loopback-only service is started
by the existing local monitor when `workbench_enabled: true`; the monitor's logon
trigger restores it after login. To start it manually: `python bdm_workbench.py`.
The service is the local working tool, not a hosted or shared website. It does not
change any public repository or publish procurement data.

Views: Active, New alerts, Awaiting decision, Preparing, Overdue, Partner preparation,
Archive and All. Cards save owner/stage/follow-up/notes/outcome and qualification:
access, capability, readiness, candidate reference, verification dates, decision,
reason and next-action date. Unknowns stay unknown. Pursue requires a reason and
next-action date; ready requires a candidate reference and dated checks. Concurrent
edits are rejected with a reload instruction. Updates are written to SQLite,
recorded in edit history and survive source refresh. Do not store CV contents.

The existing account actions are read through from actions.db. Those cards write
back to actions.db and its history in the same SQLite transaction; they are not a
second independently editable copy. Historical proposals remain in Archive. New
requirements, planning actions, RAP research and account actions remain distinct
because they represent different decisions. No speculative name-based merging.

`active_results.csv` is the daily working extract, `archived_results.csv` retains
inactive work and `results.csv`/`results.json` retain the complete ledger. Qualification
fields are included in exports; edit qualification through the working view. The
older CSV/XLSX import continues to edit only its documented owner/stage/follow-up/
notes/outcome fields. Qualification scoring uses timing and recorded human evidence;
it does not infer capability or access from public visibility. Supplier restrictions
and specific current-clearance wording are flagged for manual review.

Desktop notifications are enabled in the private local config. They request a
Windows toast for new/changed events, and a deduplicated operational warning for
failed/stale monitoring. Per-event notification receipts prevent repeat requests.
A successful Windows request does not prove the notification was read; Focus Assist
or Windows notification preferences can affect display. No historical alert flood:
`alert_since_utc` marks activation; older events are retained as suppressed_stale.
Closed/elapsed records leave active views without deleting raw source evidence,
decisions or history. The journal retains all revisions and their statuses.

A source-runner lock causes exit 75 / deferred, not a false source failure. The
local runner retries twice at 60-second intervals, then waits for the next scheduled
check. It never advances observation freshness on a deferred/failed crawl. Source
failures and staleness still need attention. Optional external heartbeat support
remains available, but no endpoint is configured: the machine cannot notify while
powered off. Awake/online/logged-in operation remains required.

Automatic email remains unconnected: no usable installed-app OAuth client/token
was found in the checked download/project locations. The code and durable Gmail
queue are ready, but a one-time Google authorization for the internal sending
mailbox is still required. Never copy chat-connector credentials into the background app.
After authorized setup, send one internal test, verify Sent reconciliation and then
enable `send_enabled`; do not replay suppressed historical alerts.

Verification and data-recovery report:
`WORKSPACE/reports/bdm_upgrade_20261110/data_audit.json`. All 7,250 BuyICT source
records matched monitor observations, without duplicate identities. Six notice
versions recovered from the official contract API resolved four missing-original
cases; six historical cases remain flagged. Source placeholder dates, missing ABNs
and other quality queues were retained, not silently guessed or deleted.

This pilot reads public BuyICT requirements, detects changes and prepares internal
alerts for Sirish. It also combines current AusTender work/plans and the ten
reviewed RAP partners into one results ledger. It does not contact buyers/partners,
submit responses, infer candidate fit or activate the legacy outreach agent.

## What is working

- Separate SQLite state: source observations, revision history, durable mail queue,
  delivery receipts and BDM actions. The first observation is a silent baseline.
- New discoveries, changed requirements/deadlines, reopenings and closures. Source
  retrieval timestamps do not cause alerts. Undelivered old revisions are superseded.
- Collector detail-page open/cancelled flags override a stale listing summary.
  Unlabelled public working-arrangement notes are retained for review.
- Public LH/PCS/RFI coverage, configurable title filters, deadline checks, Canberra
  and IST times, candidate cap, clearance, start, scope and next action in alerts.
- One results.csv/results.json, preserved owner/stage/follow-up/notes/outcome fields,
  validated edit imports and edit history. Stages: identified, qualified, preparing,
  submitted, won, lost, no fit, on hold. This is a separate pilot ledger; the existing
  action_tracker and its notes are not replaced or automatically imported.
- RAP commitments preserve review date and source-page references. Contract hooks
  are recalculated from the current intelligence database, not old register counts.
  Expired plans are labelled for successor checks.
- Monitor lock plus the existing source-runner lock. Failed/incomplete/skipped
  refreshes do not advance the BuyICT observation baseline. Expired queued work is
  suppressed before delivery. Missing rows alone never imply closure.
- Gmail send attempts persisted before the HTTP call. A crash/timeout/HTTP error
  leaves the batch unresolved and blocks automatic retries. Reconciliation searches
  Gmail Sent by the stored Message-ID. An empty search never authorizes a resend.
- Optional external heartbeat and local health.json record success/failure.

## Manual preview

Run from the source project with Python 3.11 and its existing requirements:

```text
python bdm_monitor.py poll --refresh
python bdm_monitor.py preview --sample
python bdm_monitor.py status
```

Use `--config PATH --state PATH --out PATH --data-dir PATH` BEFORE the subcommand
to relocate state and outputs. Defaults are the project database directory and
exports/bdm. `poll` without `--refresh` uses stored data for development; it does
not establish the recent live-source proof required for email delivery.

The first completed live poll silently establishes baseline. This prevents a flood
of historical requirements. Existing open work still appears in the results file.
The sample preview uses one existing open record when the queue is empty and is
labelled PREVIEW ONLY; it never creates a pending event or sends anything.
Future new discoveries are not necessarily newly published; the publication date
and source links remain available. The collector reads all public listing pages,
rechecks open details and skips closed records already final. Public listing totals
also include seller-only records that this unauthenticated collector cannot see.

## Work with the results

Open results.csv or the supplied Results workbook in Excel. Filter by source, priority, owner and stage. Edit only
owner, stage, follow_up (YYYY-MM-DD), notes and outcome. Save an edited COPY as CSV or XLSX:

```text
python bdm_monitor.py import-edits path/to/edited_results.csv
python bdm_monitor.py import-edits path/to/edited_results.xlsx
python bdm_monitor.py export
```

Imports validate all rows before committing, reject unknown IDs, and ignore
source-owned fields. Do not import an old copy over newer human edits: imports are
explicit replacements of supplied editable fields. Refreshed exports overwrite
results.csv, so save work separately and import before the next export. Formula-like
source text is escaped in CSV. Spreadsheet exports are snapshots, not live sync.

The BuyICT poll does not rebuild intelligence. It uses fresh BuyICT data directly;
AusTender plans and RAP contract hooks reflect the latest completed intelligence
build. Run the full procurement refresh weekly or fortnightly for those sources,
then run `poll` again to update planning actions. The supplied illoura-planning
service/timer templates refresh all eight sources weekly and update the ledger;
they remain inactive. They do not send a weekly digest or re-review RAP documents.

## Independent Gmail connection

The chat Gmail connector cannot be reused by a background Python service. This
adapter uses an Illoura-controlled Google Cloud OAuth client and offline user
authorization for the internal sending mailbox. The helper requests gmail.send and
gmail.readonly; read access is used for account verification and Sent reconciliation.
Google Workspace administrator approval may be needed. Choose an appropriate
internal/production OAuth setup; do not rely on short-lived external testing tokens.

1. Enable the Gmail API in Illoura's Google Cloud project and configure its consent
   screen and installed-app OAuth client. Keep the downloaded client JSON private.
2. Install requirements-bdm.txt in the selected runtime. Run manually:

```text
python bdm_authorize_gmail.py --client PRIVATE_CLIENT.json --token PRIVATE_TOKEN.json
```

3. Sign in as the internal sending mailbox. Restrict private config/token file permissions
   to the service account at the operating-system level. Never commit these files.
4. Copy config/bdm_monitor.example.yaml outside the repository. Set sender, recipient,
   oauth_token_file, approved title filters and monitoring URL there (or use
   ILLOURA_MAIL_SENDER / ILLOURA_MAIL_RECIPIENT). Real addresses never go in the repo.
   Delivery defaults to false. Do not use buyer/partner addresses: the monitor accepts
   exactly one recipient on the sender's own domain and refuses anything else.
5. When activation is requested, set send_enabled: true and use:

```text
python bdm_monitor.py --config PRIVATE_CONFIG.yaml poll --refresh --send
```

An unattended process can then authenticate and refresh access tokens without this
conversation. Revoked tokens or changed account permissions still require repair.
No actual Gmail send is used by the automated tests; live mail integration must be
verified after authorization. The preview EML is available without credentials.

## Independent execution

For a local pilot, the Windows machine must remain awake, connected and running
under an account with access to the project and token. A dedicated Task Scheduler
task can invoke the command every 15 minutes with overlap prevention and missed-run
handling. Existing AusTender scheduled tasks stay disabled until explicitly activated.
There is no task-registration side effect in the supplied PowerShell preview script.

For independence from this PC, deploy the project on an always-on Linux host with
Python 3.11, existing requirements and requirements-bdm.txt. No local AI or GPU is
needed. Provision directories and ownership for /opt/aus_tender, /etc/illoura and
/var/lib/illoura. Store data on durable local disk, not a temporary container layer.
For BuyICT alerts and stored planning, copy only needed databases (BuyICT,
intelligence, RAP evidence), approved config and
token through a private transfer; do not upload the full workspace or backups.
For the weekly full-source refresh, also transfer the required source databases
and approved project configuration; its derived intelligence needs those inputs.

deploy/illoura-buyict.service and .timer are inactive templates. Their source
databases live under /var/lib/illoura/databases. The timer checks
every 15 minutes and catches missed calendar triggers after restart. The config's
poll_minutes describes the intended interval; if changed, update the timer too.
Normal alert delay is the polling interval plus crawl and email time, subject to
source availability, overlap deferral and connectivity. This is polling, not an
official real-time push subscription. The current full public crawl is about two
minutes; benchmark this on the chosen host before committing to a service target.

Complete activation only after a live-source preview, approved filter selection,
one internal mail test, restart test and confirmed monitoring. Protect credentials
and don't publish private results in the public source repository. No hosting has
been purchased or deployed by this implementation.

## Operations and recovery

### Active local pilot (6 November 2026)

The user requested local-machine operation and explicitly selected local file
recording instead of Gmail. `IllouraBuyICTMonitor` is enabled in Windows Task
Scheduler. It runs `bdm_local_runner.py` hidden every 15 minutes and at user logon,
prevents overlap, catches missed starts and retries failures twice at five-minute
intervals. This uses an interactive user principal: the machine must be awake,
online and the user logged in. It does not change sleep settings or enable the
three legacy AusTender tasks. Standalone Gmail remains disabled by user preference.

Private configuration: inputs/bdm/monitor.yaml. Status: exports/bdm/index.html and
local_status.json. Each cycle refreshes sources, updates results and previews, then
records all event revisions (including superseded ones) in queued_messages.txt and
queued_messages.json under local_journal_dir. Each event also has an individual
immutable TXT/JSON record in messages/. No message is emailed or marked sent by
file recording. The event ID prevents duplicate file records on repeated checks.

The current local journal is in the workspace reports/buyict_local_monitor folder.
An empty log means no changes since the existing baseline; it is not an error.
The local status page checks staleness in the browser and flags successful source
checks older than 45 minutes. This is local visibility, not external monitoring
when the PC is off. To stop this pilot: `Disable-ScheduledTask -TaskName
IllouraBuyICTMonitor`. Do not delete the state database or historical messages.

- Review exports/bdm/health.json and `status`. last_live_refresh_at is the actual
  source freshness proof. The health timestamp must not be treated as fresh source
  evidence after an offline poll. Review the source report on every failed run.
- Configure an external heartbeat at a cadence allowing the 15-minute interval,
  normal crawl time and a reasonable grace period. It must alert a responsible
  person when the machine stops entirely. Local logs alone cannot do that.
- `python bdm_monitor.py --config PRIVATE_CONFIG.yaml reconcile` resolves only
  sending/uncertain batches found exactly once in Sent. Unresolved batches require
  manual investigation. Never delete the state DB or reset a send to retry blindly.
- SQLite source rows are read-only to this module. The existing collector remains
  responsible for source writes. Back up bdm_monitor.db with SQLite's backup API
  while in use, alongside the required source databases; test restoration. Losing
  mail history can cause duplicate alerts. Retain source reports/EML receipts for
  investigation and define retention before a long unattended deployment.
- A source API change or robots/access restriction requires maintenance. No bypass
  of access restrictions, scraping of secure attachments or guessed seller access.
- Candidate bench, permission to represent, clearance and commercial readiness are
  private inputs. Do not infer them from a public listing or RAP commitment.

## References

- Gmail send API: https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send
- Gmail OAuth scopes: https://developers.google.com/workspace/gmail/api/auth/scopes
- systemd timers: https://www.freedesktop.org/software/systemd/man/latest/systemd.timer.html

Daily due/overdue follow-up reminders request one desktop notification per local day, excluding closed work. Final verification: 251 full project tests passed, including HTTP origin protection, edit persistence and daily-reminder deduplication.

Legacy account rows must be edited through the working view. CSV/XLSX imports containing LEGACY: rows are rejected atomically, preventing edits to a shadow copy that the original tracker would overwrite. Source downloading now uses a separate poll lock; the edit lock is held only during reconciliation/export, so a long crawl does not lock the working view.
