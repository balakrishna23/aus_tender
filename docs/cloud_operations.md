# Illoura AWS operations

Status: 13 November 2026. Source/evidence collection runs in Sydney. The procurement
API now serves the validated cloud source generation. Private human workflow state
on the laptop has not yet been migrated into a shared cloud workbench.

## What is live

- API `/v1/catalog` includes generation ID, snapshot age, collector evidence time
  and a separate `data_stale` flag so republishing old data cannot conceal its age.
- Queries pin a generation so a concurrent publication cannot mix rows and metadata.
- `/healthz` checks process liveness. `/readyz` returns 503 if operational health is
  unhealthy or has not been checked within 30 minutes; it exposes no private details.
- Authenticated `/v1/operations` shows collection/publication freshness, run phase,
  disk space and Gmail authorization health. A healthy snapshot can still contain
  source records retrieved earlier; inspect source retrieval dates.
- `illoura-cloud-health.timer` checks every 15 minutes without sending email. A
  morning run still within 5 hours counts as `in_progress` (healthy), so `/readyz`
  stays green during normal collection. The `daily_brief` check turns `missed` when the
  brief timer is enabled and no send receipt dated that Sydney day exists by 09:00.
- The external watch (`watch deploy`) polls `/readyz` from AWS every 5 minutes and
  emails when it fails for 30 minutes; see "Independent failure detection".
- The existing Gmail send-once/reconciliation flow remains a separate reviewed step.

### Required before deploying this health version

The expected sending mailbox is private configuration, not source code. Before
installing this `illoura_cloud_health.py` / `illoura-cloud-health.service`, create
`/etc/illoura-ops/health.env` (root-owned, mode 0600) containing one line:

```text
ILLOURA_GMAIL_SENDER=<the authorised sending mailbox>
```

then `sudo systemctl daemon-reload`. Without it the mail check reports
`unconfigured`, overall health becomes `attention` and `/readyz` returns 503, which
the external watch will report. Done on 12 November 2026 (`set-health-sender`) before
this version was deployed; health reports `mail_authorization: ok`.

## Operating the server: `deploy/aws/illoura_aws.py`

One administrator CLI replaces the one-off helpers formerly kept beside the trial
report. It runs only fixed actions over SSH with a pinned host key; there is no
"run any command" option. Run from the repository root on the administrator machine:

```text
uv run --no-project --python 3.11 --with-requirements requirements-deploy.txt python deploy/aws/illoura_aws.py <action>
```

| Action | Effect |
|---|---|
| `status` | Read-only: instance, firewall, services, timer states, health, publication, disk. |
| `prune --dry-run` | Preview retention; deletes nothing. |
| `backup`, `restore-drill --backup PATH` | Operations backup; restore check into a temporary folder. |
| `publish --yes`, `rollback --yes`, `prune --yes` | Change production data; refuse without `--yes`. |
| `deploy-ops --yes` | Ship `deploy/` operations code, units, ops tests and the API table policy to a staging folder, verify SHA-256 on the server, run the tests there, then install. Refuses if `/etc/illoura-ops/health.env` lacks `ILLOURA_GMAIL_SENDER`. Records timer enable states before/after and fails if they change; never enables a timer. |
| `deploy-api --yes` | Ship `api/app.py`, run `api/test_security.py` on the server as `illoura-api`, back up the live app to `/var/backups/illoura/api-app-<time>.py`, install, restart, and restore the backup automatically if `/healthz` does not recover. Refuses dependency changes (those need a reviewed manual install). Never touches keys, snapshot pointers or the environment file. |
| `verify-ops` / `verify-api` | Stage the code and run its tests on the server (operations tests unprivileged with real symlinks; API tests as `illoura-api`), then remove the staging folder. Installs nothing; no `--yes` needed. Run before any deploy. |
| `set-health-sender --email ADDRESS --yes` | Writes `/etc/illoura-ops/health.env` (root, 0600). The address is uploaded as file content, never placed in a command line or printed. |
| `daily-brief-dry-run` | Composes a copy of the latest run under `/var/tmp` with live listing checks and layout checks, returns the text preview. Never sends. |
| `schedule enable --yes` / `schedule disable --yes` | Turns the 08:15 daily brief on or off. The only action that changes a timer's enable state. `disable` also stops a brief that has started and is waiting for 08:15, and the brief itself re-checks the timer and the 12:00 cutoff just before sending. |
| `reconcile-send --run daily_sweep_<time> --yes [--mark-undelivered]` | Operator step for a send whose result is unknown: an attempt with no receipt blocks publication and the brief every later morning until it is settled. Looks the message up in the sender's Gmail Sent folder as `scraper`: found writes the receipt; not found exits 2, and with `--mark-undelivered` records a person's decision that it was not delivered. Never sends. Each morning the collection coordinator first runs the same lookup automatically, but only a message confirmed in Sent is settled that way; "not found" always needs a person. |
| `harden-os --yes` | Ubuntu upkeep away from the 06:15-08:15 Sydney brief: needrestart never restarts the Illoura jobs (`deploy/system/needrestart-illoura.conf`), security updates run 03:30-04:00 Sydney (`deploy/system/apt-daily-upgrade-illoura.conf`); clears known stale failed units. |
| `os-update --yes [--reboot]` | Installs pending security updates now; with `--reboot`, reboots if one needs it and waits for `/readyz`. Refuses 05:30-09:30 Sydney and while the brief or a collection runs. API offline for about a minute. |
| `api-key create --name NAME` / `api-key revoke --name NAME` | Named team credentials. The key is written only to the private directory; the server stores its hash. |
| `verify-collectors` / `deploy-collectors --yes` | Collector code (`run_spiders.py`, its standalone source scripts `austender_api.py` and `nsw_tender_fetch.py`, and `spiders/*.py` only): stage, hash-check, run every `tests/test_collectors_*.py` on the server against the staged code, then install only changed files as `scraper`, keeping the originals in `/var/backups/illoura/collectors-<time>/`. Refuses while the brief or collection runs. |
| `set-mail-config --yes` | Creates `/etc/illoura-routine/mail.env` (root:scraper 0640: sender, recipient, link id) from the values in the deployed routine scripts. The values never leave the server and are never printed; an existing file is never replaced. Done 13 Nov. |
| `verify-routine` / `deploy-routine --yes` | Newsletter routine (`routine/run_once.py`, `routine/scripts/*.py`; never `routine/legacy/`): stage, hash-check, run every `tests/test_routine_*.py` on the server, then install only changed files as root:scraper 0640, originals in `/var/backups/illoura/routine-<time>/`. Refuses while the brief or collection runs and before `mail.env` exists. |
| `watch deploy --yes` / `watch status` | External `/readyz` watch: CloudFormation stack `illoura-watch` (Lambda every 5 minutes, CloudWatch alarm, SNS email). The address defaults to the verified Lightsail alarm contact and is never printed; the new subscription must be confirmed from the email AWS sends. |
| `snapshots enable --yes` / `snapshots status` | Daily Lightsail automatic snapshots of the whole server at 23:00 UTC (after the brief); Lightsail keeps the newest 7. About USD 0.05 per GB-month of used, changed data. |

Settings come from environment variables (`ILLOURA_AWS_PROFILE`, `ILLOURA_AWS_REGION`,
`ILLOURA_AWS_INSTANCE`, optional `ILLOURA_AWS_ACCOUNT` check, `ILLOURA_PRIVATE_DIR`);
no account ID is stored in the repository. `publish`/`prune` may exit 1 with
"published, retention needs attention"; read the printed JSON.

The API service source and its security tests are in `api/`. `api/install-api.sh` and
`api/enable-https.sh` are first-install provisioning records only: rerunning
`install-api.sh` would repoint `current` at the original snapshot, so use `deploy-api`
for updates. `deploy/aws/cloud-init.sh` is the POSIX first-boot script (checked with
`sh -n`). The daily newsletter routine helpers remain in the private workspace.

## Daily brief at 08:15 Sydney

Requested by Sirish on 12 November 2026 and enabled the same night
(`illoura-daily-brief.timer`, first run Friday 13 November). It replaces the separate
collection timer, which stays disabled so collection never runs twice.

1. 06:15 Australia/Sydney: `illoura_daily_brief.py run` (root) runs the existing collection
   coordinator: eight sources, research, evidence, backup, restore drill, publication with
   retention. Any failure stops the day; the health service records it.
2. As `scraper`: selects up to eight open, relevant BuyICT ICT labour-hire requests closing
   soonest (18 hours to 21 days away; professional services fill in if fewer than three),
   rechecks each public listing live, drops any that fail, adds "New on BuyICT since the last
   run", writes templated editorial notes, composes, and runs desktop/mobile layout checks.
3. Records an automated review (content hashes, layout result, selection) and waits for 08:15.
4. Just before sending, re-checks that the timer is still enabled and that it is before
   12:00 Sydney on the edition's day; otherwise keeps the brief for review. Sends once
   through the existing helper, which refuses a BuyICT gap or an unclear source report, stale edition dates, changed
   content, identity mismatch and duplicates (Gmail Sent is checked by subject). Since
   13 November one edition date can be sent only once across all run folders, under a
   server-wide `send.lock`, and an unresolved attempt in another folder blocks the send
   until `reconcile-send` settles it. If most live listing checks fail (for example a
   BuyICT outage) the brief is not approved, rather than reporting "0 requests".
   If the brief is not ready by 12:00 Sydney the day is skipped, not sent late. The timer
   has no catch-up after downtime.

Ubuntu's own upkeep stays out of this window (`harden-os`): security updates run at
03:30-04:00 Sydney, and needrestart never restarts the Illoura jobs after an upgrade.

The email says plainly that selection was automatic and that invitation access, secure
attachments and candidate fit are unverified. It goes only to the internal recipient fixed
in the server's private send helper; it never contacts buyers or partners.
Outputs: `/opt/illoura-routine/reports/daily_sweep_<IST time>/` (preview, screenshots,
review, receipt). Rehearse with `daily-brief-dry-run`; stop with `schedule disable --yes`.

## Collection and publication

Use the coordinator as the supported cloud write entry point:

```sh
sudo systemctl start illoura-cloud-collect.service
sudo journalctl -u illoura-cloud-collect --no-pager
```

The service invokes the existing source/evidence runner as `scraper`, then takes
application backups and publishes a verified snapshot as root. It does not compose
or send a newsletter. Collection, operations and source locks prevent overlapping
supported writers; do not launch individual source CLIs independently on this host.
Independent per-database SQLite backups are not one cross-database transaction.

Publication requires a report from all eight sources with no problem in BuyICT (other
sources with a problem are recorded as `source_gaps` and keep their last good rows) and essential evidence
steps, plus an intelligence build timestamp matching the evidence. Missing tables,
failed checks, inadequate free disk space and invalid evidence prevent switching
the served generation. Files and directories are flushed before the atomic symlink
switch. The prior generation stays available. Failed staging is retained for review.

Retention is deployed (12 November 2026, workstream 1; see
[the retention work log](workstreams/01-retention.md)). It keeps current, previous
and three newest other complete generations; expires failed staging strictly after
two days; and retains fourteen checksum-validated operations backups. Standalone
`prune --dry-run` previews deletion without writing a report. Successful publication
invokes pruning under the existing write locks. Cleanup failure reports
`published=true` and `retention_attention=true` rather than undoing a valid
publication. Invalid/incomplete copies are retained for manual attention. The API
request path is unchanged and has no added reader lock or grace period.

A source that finds nothing new is not a failure when its collector says so
(`crawl/nothing_due`): standing offers with no new or due panels, and contract
notices when the API has nothing new or every new notice has no page on the site
yet (up to 25; see [workstream 8](workstreams/08-reliability.md)). Any other empty
or failed source still stops publication and the day's brief.

### Research dataset (`research`)

Added 24 November 2026. The read API and the Claude Desktop connector also serve the
private research database (PostgreSQL) as the `research` dataset, so an API key reaches
registers, APS headcount, hiring demand, remoteness, the RAP directory, panels and the
mailbox business inputs as well as the tender data.

The same day the API also began serving three internal workflow files that were already
in `/opt/aus_tender/databases` and published with each generation: `actions` (BD action
items), `activity` (collection runs, scrapes and change records) and `research_sources`
(checked source pages, reviewed findings, BuyICT news). Every enabled key sees every
dataset; there is no per-key scope.

`deploy/illoura_research_export.py` writes it. Before each publication the coordinator
runs `install`. That runs `export` as `ubuntu` with the research interpreter: one
`REPEATABLE READ, READ ONLY` transaction, current state only (no raw snapshot history),
individuals and sole traders in the business registers left out. `install` then checks
the file (integrity, every table present, core tables not empty) and atomically replaces
`/opt/aus_tender/databases/research.db`. It is bounded (300 seconds), skips itself when
the morning budget is short, and never stops publication. On any failure the previous
`research.db` is published unchanged, so the data can be up to a day older than the tender
tables. Check `research.export_info` for the export time and `research.sources` for each
source's snapshot, date and licence.

Refresh it by hand (root, outside 05:30-09:30 Sydney), then publish or wait for the next
morning's publication:

```sh
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_research_export.py install
```

`research.db` must exist before `policy.json` lists `research`. The publish step requires
every listed file, so create it by hand once before the first `deploy-ops` that ships this
policy.

### Publication exit status

`publish` exits **1** when retention requires attention even if the new generation
was successfully published. Inspect the JSON result: `published=true` together
with `retention_attention=true` means publication succeeded and cleanup needs repair.
Do not automatically retry publication or roll back that valid generation because
of the exit code alone. Inspect `retention.errors`, repair the cause and retry the
standalone `prune` command. A failed pre-publication check leaves current unchanged.

### Clearing persistent retention attention

A corrupt or incomplete `operations-*` directory is deliberately retained and
reported, so it can keep health in `attention` on every prune. To resolve it:

1. Identify the exact directory from `retention.json`; inspect its manifest and
   checksum failures. Do not edit the manifest to conceal corruption.
2. Create a replacement backup and verify it with `restore-drill`. Preserve and
   reconcile any unique mail attempts/receipts from the suspect copy first. Until
   the later restore-status fix, inspect `unresolved_send_runs` explicitly even if
   the drill reports `passed` or exits zero.
3. Quarantine the exact suspect directory outside the managed backup root while
   holding the same locks as publication/pruning. Example commands below are for
   an operator after replacing `operations-EXACT_ID` with the reviewed directory.
4. Run `prune --dry-run`, then `prune`. Refresh the health check once and inspect
   `/v1/operations`. Quarantine preserves evidence; deleting it is a separate
   operator decision after recovery/mail-state reconciliation.

```sh
sudo install -d -o root -g root -m 0700 /var/backups/illoura-quarantine
sudo flock -n /opt/illoura-routine/operations.lock \
  flock -n /opt/illoura-routine/routine.lock \
  flock -n /opt/aus_tender/logs/.run.lock \
  mv -- /var/backups/illoura/operations-EXACT_ID /var/backups/illoura-quarantine/
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py prune --dry-run
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py prune
sudo systemctl start illoura-cloud-health.service
```

These commands apply after the approved retention code is deployed. They do not
enable a timer. If a lock is busy, stop and retry later; do not bypass it. Verify
the source is a real direct-child backup directory, not a symlink, before moving.

### Crash leftovers

Temporary `.current-*`, `.previous-*` and `.rollback-*` symlinks left by a crash are
harmless and outside the pruning policy. They do not replace the authoritative
`current`/`previous` pointers and do not duplicate the database contents. Pruning
does not delete them. Avoid broad glob cleanup that might touch live pointers.

For a completed run without new collection:

```sh
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py publish
```

`latest_run.json` must identify the intended successful run. Do not reuse a sent
edition as a new email authorization or overwrite its artifacts.

The standalone `illoura-cloud-collect.timer` stays **disabled**: since 12 November
the daily brief (06:15 Sydney) runs collection itself, so collection never runs
twice. This is separate from the old local monitor.

## Recovery

Application backup and isolated restore verification:

```sh
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py backup
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py restore-drill --backup /var/backups/illoura/operations-EXACT_ID
```

Portable backups include cloud action/activity/review databases, selected reviewed
configuration, and run/send-attempt/send-receipt evidence. They exclude OAuth/API
credentials and do not replace full source-data or machine backups. A hash-verified
copy is stored outside the server in the operator's protected private recovery folder.
The actual drill restored six databases and preserved one sent receipt and attempt.
It did not restore a whole VM or send mail.

After a disaster, stop collector/monitor/mail jobs first. Restore into a separate
directory, verify hashes and SQLite integrity, then reconcile every pending or
uncertain send against Gmail Sent. Do not clear `send_attempt.json` to permit a retry.
The drill reports unresolved send runs and always requires reconciliation before
resuming transport. Credentials need the separately protected private copy or new
consent; never place them in ordinary exports.

One-step snapshot rollback (verifies hashes and SQLite before switching):

```sh
sudo /opt/aus_tender/.venv/bin/python /opt/illoura-ops/illoura_cloud_ops.py rollback
```

Rollback targets `previous`. It is deliberately not an alternating undo/redo toggle;
repeat invocation selects the same previous generation. Source databases are unchanged.

An AWS-managed instance snapshot `illoura-before-cloud-ops-20261112` was created
before deployment and its state was confirmed `available`. Since 13 November,
Lightsail automatic snapshots run daily at 23:00 UTC (`snapshots status`); Lightsail
keeps the newest seven. They are stored by AWS outside the instance, so losing the
server loses at most a day. Whole-VM recovery (create a new instance from a
snapshot, reattach the static IP) has not been exercised. Verify a snapshot's AWS
state before relying on it.

## Independent failure detection

An AWS-managed Lightsail alarm `illoura-trial-server-unhealthy` watches instance
status checks and missing metrics. Email notification is configured to the existing
internal recipient; AWS contact status is now `Valid`. The configured alarm test
succeeded on 12 November; inbox delivery/read has not been independently verified. This alarm
detects server status problems; it does not verify application/Gmail correctness.

Since 13 November the CloudFormation stack `illoura-watch` (template
`deploy/aws/illoura-watch.yaml`, deployed with `watch deploy --yes`) polls `/readyz`
from AWS Lambda every 5 minutes and records `Illoura/Watch ReadyzOk`. Alarm
`illoura-watch-needs-attention` emails through SNS when most checks (at least 2 of every 3)
fail for two 15-minute periods, or when checks stop arriving, and again on recovery. It covers
the server being down, a failed or hung collection, a failed publication, lost Gmail
authorisation, low disk, retention attention and a missed 08:15 brief. It does not
depend on the server or on its Gmail token. The SNS subscription was confirmed on
13 November and alarm/recovery emails were delivered that day; check with `watch status`.
Health also checks the HTTPS certificate (about 6.7-day lifetime, renewed by certbot
with about 2 days left, twice-daily attempts): under 24 hours left makes `/readyz` fail,
so a failing renewal is reported about a day before the certificate expires.
`deploy/illoura_external_watch.py` remains a manual probe for the same endpoint.

## Validation and scope

Seven operations tests passed on Linux: failed source/table publication protection,
rollback, incomplete build-evidence rejection, backup corruption, and preservation
of both resolved and unresolved send states. Three health tests covered failed/hung
collection and missing mail credentials. Nine API tests passed, including
authentication/write/table restrictions, MCP, bounded queries, generation consistency
and readiness staleness. Three Linux symlink tests are skipped on Windows.

Claude reviewed implementation source supplied without credentials. Review fixes
included mandatory build metadata, directory durability and explicit unresolved-send
reporting. An actual Claude Code conversation called the live MCP catalog and SQL
tools and returned the new generation and 429,833 contracts. Desktop UI interaction
is a separate untested surface. No new newsletter or buyer/partner outreach was sent.
