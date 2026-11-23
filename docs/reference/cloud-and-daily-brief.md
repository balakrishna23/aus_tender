# Reference: cloud operations and the daily brief

Run scheduled collection, publish recoverable snapshots and send one checked internal daily brief.

Files covered:

- `deploy/illoura_cloud_collect.py`
- `deploy/illoura_cloud_ops.py`
- `deploy/illoura_cloud_health.py`
- `deploy/illoura_daily_brief.py`
- `deploy/illoura_external_watch.py`
- `deploy/package_snapshot.py`
- `deploy/restore_snapshot.py`
- `deploy/package_for_vps.py`
- `deploy/routine_mail_env.py`
- `deploy/setup_vps.sh`
- `deploy/check_vps.sh`
- `deploy/register_windows_task.ps1`
- `deploy/run-bdm-preview.ps1`
- `deploy/austender-scraper.service`, `deploy/austender-scraper.timer`
- `deploy/illoura-buyict.service`, `deploy/illoura-buyict.timer`
- `deploy/illoura-planning.service`, `deploy/illoura-planning.timer`
- `deploy/illoura-cloud-collect.service`, `deploy/illoura-cloud-collect.timer`
- `deploy/illoura-cloud-health.service`, `deploy/illoura-cloud-health.timer`
- `deploy/illoura-daily-brief.service`, `deploy/illoura-daily-brief.timer`
- `deploy/illoura_research_refresh.py`, `deploy/illoura-research-refresh.service`, `deploy/illoura-research-refresh.timer`, `deploy/research-refresh-handoff.md`
- `deploy/system/apt-daily-upgrade-illoura.conf`
- `deploy/system/needrestart-illoura.conf`
- `deploy/aws/illoura_aws.py`
- `deploy/aws/cloud-init.sh`
- `deploy/aws/illoura-watch.yaml`
- `routine/README.md`
- `routine/run_once.py`
- `routine/scripts/build_rap_partner_register.py`
- `routine/scripts/check_newsletter_layout.py`
- `routine/scripts/collect_buyict_news.py`
- `routine/scripts/collect_official_research.py`
- `routine/scripts/compose_checked_brief.py`
- `routine/scripts/export_five_buyict_requirements.py`
- `routine/scripts/finalize_daily_sweep.py`
- `routine/scripts/mail_config.py`
- `routine/scripts/newsletter_research_sweep.py`
- `routine/scripts/prepare_newsletter_evidence.py`
- `routine/scripts/refresh_daily_shortlist.py`
- `routine/scripts/render_newsletter_preview.py`
- `routine/scripts/send_gmail.py`
- `routine/legacy/complete-cloud-review.py`
- `routine/legacy/send-reviewed-cloud-brief.py`

The daily path is timer → source collection and research → evidence gates → atomic snapshot publication → public shortlist checks → compose/layout/review hashes → wait until 08:15 Australia/Sydney → internal Gmail send → confirmed receipt. It starts at 06:15 Sydney and does not catch up missed timer firings. Failed collection cannot replace served data or authorize a brief. The sender rechecks the enabled timer and date/window; noon is the cutoff. Sirish's internal brief is separate from buyer/partner outreach. Current server state, timer activation and delivery are unverified by this static review; see [cloud operations](../cloud_operations.md) for operational procedures and the full command table.

| Deployment module | Responsibility |
|---|---|
| `illoura_cloud_collect.py` | Reconciles unresolved prior sends against Gmail Sent as the scraper user, runs source/evidence coordination and publishes a validated generation. Collection/publication sends no new brief. Records unresolved reconciliation failures for attention. |
| `illoura_cloud_ops.py` | Linux locks coordinate publication/recovery/retention. Validates all source results and evidence/build timestamps; uses SQLite backup into a staged immutable generation, integrity/hash checks and atomic `current`/`previous` pointers. Implements publication, rollback, operations backup, isolated restore drill and retention. Restore preserves send receipts, unresolved attempts and explicit reconciliation decisions without credentials. |
| `illoura_research_refresh.py` | Root wrapper for the daily research refresh (timer 10:15 Sydney, installed disabled). Takes the shared `/var/lib/illoura-market-refresh/refresh.lock` without waiting, skips the 05:30–09:30 Sydney brief window (checked again after locking), runs `market_intelligence research-refresh` as ubuntu with nice/ionice and a 45-minute process-group limit, and writes `/var/lib/illoura-research-refresh/status.json`. Health reads that file as `research_freshness`, which is informational only: it never changes health status or sends mail. |
| `illoura_cloud_health.py` | Writes private health JSON and exits nonzero on attention. Reads source/publication/run/coordinator state, timer/delivery status, disk/retention/certificate state and Gmail identity authorization. It may refresh OAuth and query Gmail profile but never sends mail. False-alarm guards (14 Nov): a receipt dated on or after the due day counts; while `illoura-daily-brief.service` is still active the brief is `pending` until 12:00 Sydney and a late publication (up to 32 h old) is `in_progress`; a network or Google-side error in the Gmail check is tolerated for 6 h after the last confirmed check (`verified_at_utc`), while a revoked or expired grant fails at once. |
| `illoura_daily_brief.py` | Root coordinator invokes collection/publication; composition and mail helpers run as `scraper`. Selects near-term open labour-hire RFQs, fills with professional services, checks current public evidence and records access caveats. Builds editorial notes, renders/layout-checks, records review hashes, locks one send per edition, reconciles uncertain attempts, and records sent state. `dry-run` composes a copy without delivery. Collection (limit 2 h) has four retries after 5/20/40/60-minute pauses; composition (limit 15 min) has one retry after 5 minutes. Retries must start by 09:30 Sydney and fit before the noon cutoff; the send and the unresolved-sends guard are never blindly retried. |
| `illoura_external_watch.py` | Separate-host readiness probe, exits 0/1. Must be wired to an independent alert destination; running it on the monitored machine cannot detect that machine's complete outage. |
| `package_snapshot.py` | Full team or `--public` snapshot with SQLite-consistent copies. Full mode includes private reviewed inputs/artifacts; public mode restricts datasets and scrubs personal/internal fields. Excludes secrets and the imported Sheets mirror. Supports optional business documentation. Packaging code can invoke version-control tools; do not run it during a static-only review. |
| `restore_snapshot.py` | Restores allowed data/artifact paths from an archive; ignores archived code. Keeps existing databases unless `--force` is supplied. |
| `package_for_vps.py` | Legacy complete VPS archive, including `.env`, configured Google credentials and databases. Treat the resulting archive as private; it is not a public snapshot. |
| `routine_mail_env.py` | One-time extraction of existing deployed mail settings into `/etc/illoura-routine/mail.env`. Refuses replacement; writes root-owned, scraper-readable mode 0640. Reports key names without values. |
| `setup_vps.sh` | Legacy Ubuntu installation: creates scraper/runtime, seeds databases only if absent, installs browser/dependencies and enables the six-hour scraper timer. It has activation side effects. |
| `check_vps.sh` | Reports legacy schedule, latest result, releases and resources; `--run` starts the scraper service before reporting. |
| `register_windows_task.ps1` | Registers scraper/backup every six hours, outreach agent every 15 minutes and a business-window hourly BuyICT refresh. Uses hidden tasks, overlap prevention and missed-start handling. This legacy registration can enable outreach and must not be treated as a preview. |
| `run-bdm-preview.ps1` | Manual BDM refresh then preview with selected Python; no task registration and no email. Reads `ILLOURA_BDM_PYTHON` with a local-install fallback. |
| `aws/illoura_aws.py` | Administrative fixed-action CLI. Validates AWS account, pins SSH host identity, uploads staged files and hashes, runs staged tests, then installs only allowed changed files. API deploy has rollback on unhealthy replacement; dependency changes are refused by its deployment policy. No arbitrary remote shell action and no timer enabling as a deploy side effect. |
| `aws/cloud-init.sh` | Minimal Ubuntu base, runtime user and app directories, readiness marker; installs no application timers. |
| `aws/illoura-watch.yaml` | CloudFormation for an independent scheduled Lambda readiness probe, logs, metric/alarm and SNS email subscription. Parameters `ReadyzUrl`, `AlertEmail`; Lambda environment `READYZ_URL`. Missing metric data also alarms; subscription confirmation is an external prerequisite. |
| `system/apt-daily-upgrade-illoura.conf` | Resets unattended-upgrade calendar to 03:30 Sydney with 30-minute jitter, outside the brief window. |
| `system/needrestart-illoura.conf` | Prevents package-maintenance restarts of daily-brief/collection/health oneshot units; coordinated maintenance remains an operator action. |

Retention protects the current and previous generation plus three newest older generations and active reader locks. It retains 14 complete operations backups; failed staging older than two days can be removed. Invalid/escaping pointers and overlapping roots stop unsafe deletion. Cleanup failure reports attention after a successful publication without undoing that publication. `--dry-run` does not delete or write a retention report.

| Unit pair | Schedule | Service user and limits |
|---|---|---|
| `austender-scraper` | 00:15, 06:15, 12:15, 18:15 Sydney; persistent, jitter up to 300s. | `scraper`; collector timeout 14,400s per source, one retry; backup `--keep 14` afterward; Nice 10. `SuccessExitStatus=0 1` means systemd success alone does not establish collection success. |
| `illoura-buyict` | Every 15 calendar minutes; persistent; accuracy 30s. | `scraper`; monitor `poll --refresh --send` using private config/state; 25min timeout; private tmp, no-new-privileges, umask 0077. Configuration still gates mail. |
| `illoura-planning` | Monday 06:00 Sydney; persistent. | `scraper`; full source refresh with `--no-sync`, then monitor poll without send; 32h timeout, private tmp, no-new-privileges, umask 0077. |
| `illoura-cloud-collect` | 08:00 Asia/Kolkata; persistent, jitter 60s. | No `User=`: root for a system unit; 5h, 5G memory, CPU quota 150%, private tmp, no-new-privileges, umask 0077. On failure invokes health. |
| `illoura-cloud-health` | Five minutes after boot, then 15 minutes after activation. | No `User=`: root; optional `/etc/illoura-ops/health.env`; 90s, 256M memory, private tmp, no-new-privileges, umask 0077. |
| `illoura-daily-brief` | 06:15 Sydney; nonpersistent; sender waits until 08:15. | No `User=`: root coordinator; 6h, 5G memory, CPU quota 150%, private tmp, no-new-privileges, umask 0077. Compose/send delegated to `scraper`; failure invokes health. |

| Routine file | Inputs → output / role |
|---|---|
| `README.md` | Routine usage and deployment context. |
| `run_once.py` | Linux-only top-level coordinator with `fcntl` lock. Writes baseline counts/BuyICT IDs and run state; runs all eight sources and supplemental research concurrently; requires a report from all eight sources with the final feed, no problem in BuyICT and a run that was not cut short (`source_gate`; other sources with a problem are written to `source_gaps` in the run state and do not stop the day), plus successful mandatory evidence stages, before `ready_for_editorial_review`. Never sends. Stage deadlines: each spider 45 min, sources 60, research 60, finalization 45, whole routine 105; a timed-out stage has its whole process tree killed and is recorded as failed. |
| `scripts/newsletter_research_sweep.py` | Runs allowlisted official collection, BuyICT news, curated-source checks and WA contracts; writes per-step research status/logs. Supplemental failures are reported separately from mandatory source gates. |
| `scripts/collect_official_research.py` | Robots-aware allowlisted official HTML/PDF collection → `research_sources.db/source_pages` and collection manifest/provenance. No login, form submission or outreach. |
| `scripts/collect_buyict_news.py` | Anonymous observed public widget calls → BuyICT news rows and research manifest. |
| `scripts/finalize_daily_sweep.py` | Waits for eight source results and final feed; runs/reuses intelligence build, quality audit, optional hybrid index and newsletter evidence. `LOCAL_AI_DISABLED` skips indexing. Records step exits; caller checks stage results rather than trusting exit zero alone. |
| `scripts/prepare_newsletter_evidence.py` | Read-only extraction of candidate opportunities, renewals, MMR/RAP/research and quality/context → `newsletter_evidence.json`. Supplies evidence for editorial selection, not automatic claims of readiness. |
| `scripts/refresh_daily_shortlist.py` | `--out` and `--ids` → current public BuyICT detail checks, per-ID evidence/hash files and shortlist manifest. No authenticated submissions or private attachments. |
| `scripts/compose_checked_brief.py` | Run evidence + shortlist + editorial notes → newsletter editorial package, preview/review and outbound-send request. Loads private mail settings, describes supplemental research gaps and distinguishes current opportunities from contextual research. Does not itself deliver. |
| `scripts/render_newsletter_preview.py` | Editorial JSON → escaped HTML, text, unsent EML and review metadata. No mail transport or credentials. |
| `scripts/check_newsletter_layout.py` | Browser rendering at desktop/mobile widths → screenshots/check JSON; flags overflow. Requires browser runtime. |
| `scripts/send_gmail.py` | Requires all eight named source results, a valid completed feed, BuyICT OK, exact coordinator-approved unrelated source gaps, both layout checks, explicit current review, matching content hashes/date/recipient and authorized Gmail identity. Checks Sent using complete case-insensitive addresses and the SENT label before attempting, writes `send_attempt.json` before POST, confirms SENT and records `send_receipt.json`. Existing receipt or unresolved attempt blocks another send. Linux lock protects the run. |
| `scripts/mail_config.py` | `/etc/illoura-routine/mail.env`, overridden by `ILLOURA_MAIL_*` environment values → validated plain sender/recipient and optional link identifier. Missing/malformed settings fail closed. |
| `scripts/build_rap_partner_register.py` | Human-reviewed findings and cited documents plus contract context → `rap_evidence.db` review/commitment tables and report. No crawl/model/email/source-write effects. |
| `scripts/export_five_buyict_requirements.py` | Fixed five public RFQ checks → requirement/source files, CSVs and package manifest. Partial export explicitly excludes secure seller attachments. Historical utility, not the general shortlist selector. |
| `legacy/complete-cloud-review.py` | Historical edition-specific assertions and artifact updates to reviewed-but-mail-authorization-blocked state. Not timer code; not a general review command. |
| `legacy/send-reviewed-cloud-brief.py` | Historical fixed-evidence hashes/shortlist/freshness checks, creates review artifacts and calls sender. Contains edition-specific assumptions; not a reusable or timer send entry point. |

`routine/scripts/test_sender.py` is documented once, on [tests.md](tests.md). An uncertain mail attempt must be reconciled, never deleted to force a retry. The cloud wrapper checks identity and searched headers before recording delivery; an explicit operator decision is required to mark a verified-not-found attempt undelivered. The send lock spans helper invocation and state update.

| Health check | Failure/attention condition |
|---|---|
| Certificate | Missing/unreadable/expired certificate or less than a day of validity. |
| Publication and collection | Timestamp missing, future or older than 26 hours; an in-progress morning run has a bounded grace period. |
| Latest run | Failed phase remains visible even when the previous publication is fresh. |
| Coordinator | Running for five hours or invalid/non-completed state. |
| Daily brief | Enabled schedule lacks a confirmed same-Sydney-day send by 09:00. Disabled schedule does not demand delivery. |
| Disk | Free space is not above both 10 GiB and 15%. |
| Retention | Present report is malformed or records failure; reports reclaimed and retained space. |
| Mail authorization | Missing sender configuration/token, refresh/profile failure or sender identity mismatch. No test message is sent. |
| Public readiness | `/readyz` rejects missing, failed or 30-minute-old health evidence. Independent watch detects silence as well as failure. |

CLI examples below are documentation only. Prefix each action with `python deploy\aws\illoura_aws.py` on Windows. Real private values belong in private configuration. Full options and recovery sequences are in [cloud operations](../cloud_operations.md).

| One-line command | Purpose |
|---|---|
| `python deploy\aws\illoura_aws.py status` | Read operational state. |
| `python deploy\aws\illoura_aws.py publish --yes` | Publish validated complete evidence. |
| `python deploy\aws\illoura_aws.py backup --yes` | Create operations recovery backup. |
| `python deploy\aws\illoura_aws.py rollback --yes` | Swap to the previous validated generation. |
| `python deploy\aws\illoura_aws.py prune --dry-run` | Review retention candidates. |
| `python deploy\aws\illoura_aws.py prune --yes` | Apply guarded retention. |
| `python deploy\aws\illoura_aws.py restore-drill --backup BACKUP_PATH` | Test managed backup in isolation. |
| `python deploy\aws\illoura_aws.py verify-ops` | Stage/test operations without installation. |
| `python deploy\aws\illoura_aws.py deploy-ops --yes` | Stage/test/install operations. |
| `python deploy\aws\illoura_aws.py verify-api` | Stage/test API without installation. |
| `python deploy\aws\illoura_aws.py deploy-api --yes` | Stage/test/install API with health rollback. |
| `python deploy\aws\illoura_aws.py verify-collectors` | Stage/test collectors without collection/install. |
| `python deploy\aws\illoura_aws.py deploy-collectors --yes` | Stage/test/install allowlisted collector changes. |
| `python deploy\aws\illoura_aws.py verify-routine` | Stage/test routine without installation. |
| `python deploy\aws\illoura_aws.py deploy-routine --yes` | Stage/test/install routine scripts. |
| `python deploy\aws\illoura_aws.py api-key create --name KEY_NAME` | Create private access credentials. |
| `python deploy\aws\illoura_aws.py api-key revoke --name KEY_NAME` | Revoke named key. |
| `python deploy\aws\illoura_aws.py set-health-sender --email PRIVATE_VALUE --yes` | Set private health identity. |
| `python deploy\aws\illoura_aws.py set-mail-config --yes` | Migrate existing deployed private mail settings once. |
| `python deploy\aws\illoura_aws.py daily-brief-dry-run` | Compose a copy, no send. |
| `python deploy\aws\illoura_aws.py watch status` | Inspect independent watch. |
| `python deploy\aws\illoura_aws.py watch deploy --yes` | Deploy external watch; optional `--email` is private. |
| `python deploy\aws\illoura_aws.py snapshots status` | Inspect whole-server snapshot schedule. |
| `python deploy\aws\illoura_aws.py snapshots enable --yes` | Enable daily infrastructure snapshots. |
| `python deploy\aws\illoura_aws.py schedule enable --yes` | Enable daily brief timer explicitly. |
| `python deploy\aws\illoura_aws.py schedule disable --yes` | Disable daily brief timer. |
| `python deploy\aws\illoura_aws.py harden-os --yes` | Install guarded maintenance scheduling. |
| `python deploy\aws\illoura_aws.py os-update --yes` | Update outside protected morning window; optional `--reboot`. |
| `python deploy\aws\illoura_aws.py reconcile-send --run RUN_NAME` | Check uncertain send against Sent without sending. |
| `python deploy\aws\illoura_aws.py reconcile-send --run RUN_NAME --mark-undelivered --yes` | Explicitly resolve verified non-delivery. |
