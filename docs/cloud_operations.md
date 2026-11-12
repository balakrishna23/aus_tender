# Illoura AWS operations

Status: 12 November 2026. Source/evidence collection runs in Sydney. The procurement
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
- `illoura-cloud-health.timer` checks every 15 minutes without sending email.
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
the external watch will report. The version deployed on 12 November still carries
its own value and is unaffected until this version is installed.

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
| `api-key create --name NAME` / `api-key revoke --name NAME` | Named team credentials. The key is written only to the private directory; the server stores its hash. |

Settings come from environment variables (`ILLOURA_AWS_PROFILE`, `ILLOURA_AWS_REGION`,
`ILLOURA_AWS_INSTANCE`, optional `ILLOURA_AWS_ACCOUNT` check, `ILLOURA_PRIVATE_DIR`);
no account ID is stored in the repository. `publish`/`prune` may exit 1 with
"published, retention needs attention"; read the printed JSON.

The API service source and its security tests are in `api/`. `api/install-api.sh` and
`api/enable-https.sh` are first-install provisioning records only: rerunning
`install-api.sh` would repoint `current` at the original snapshot, so use `deploy-api`
for updates. `deploy/aws/cloud-init.sh` is the POSIX first-boot script (checked with
`sh -n`). The daily newsletter routine helpers remain in the private workspace.

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

Publication requires all eight successful source results and essential evidence
steps, plus an intelligence build timestamp matching the evidence. Missing tables,
failed checks, inadequate free disk space and invalid evidence prevent switching
the served generation. Files and directories are flushed before the atomic symlink
switch. The prior generation stays available. Failed staging is retained for review.
Generations are not pruned automatically yet; monitor disk use and agree retention
before prolonged scheduled operation.

Retention implementation is now available in the working tree for workstream-1
review, but has **not** been deployed or run against production storage. See
[the detailed retention work log](workstreams/01-retention.md). The proposed code
keeps current, previous and three newest other complete generations; expires failed
staging strictly after two days; and retains fourteen checksum-validated operations
backups. Its standalone `prune --dry-run` previews deletion without writing a report.
Successful publication invokes pruning under the existing write locks. Cleanup
failure reports `published=true` and `retention_attention=true` rather than undoing
a valid publication. Invalid/incomplete copies are retained for manual attention.
The API request path is unchanged and has no added reader lock or grace period.

Workstream 1 was approved after code review on 12 November 2026. Approval does not
deploy the working-tree implementation; the deployed version remains unchanged.

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

The daily collection timer is installed but **disabled**. Its proposed cadence is
08:00 Asia/Kolkata daily, with a short randomized delay. Activate only after the
outage-alert contact is verified, retention/restore arrangements accepted, and the
user's cadence decision is recorded. This is separate from the old local monitor.

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
before deployment and its state was confirmed `available`. Whole-VM recovery has
not been exercised. Verify a snapshot's AWS state before relying on later backups.

## Independent failure detection

An AWS-managed Lightsail alarm `illoura-trial-server-unhealthy` watches instance
status checks and missing metrics. Email notification is configured to the existing
internal recipient; AWS contact status is now `Valid`. The configured alarm test
succeeded on 12 November; inbox delivery/read has not been independently verified. This alarm
detects server status problems; it does not verify application/Gmail correctness.

`deploy/illoura_external_watch.py` can poll HTTPS `/readyz` from a different host.
It returns exit 1 on network failure, stale health or unhealthy data/auth state.
The probe was tested from the operator's computer. A continuously hosted external
readiness monitor and its independent delivery channel are not yet configured.
The in-instance health timer alone cannot notify when its own machine is down.

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
