# Runbook: when something goes wrong

Short, practical steps for the failures this system can have. Run every command from
`C:\work\aus_tender` on the team PC:

```powershell
uv run --no-project --python 3.11 --with-requirements requirements-deploy.txt python deploy/aws/illoura_aws.py <command>
```

Below, `illoura_aws.py <command>` means that full line. If AWS says the session expired, run
`aws login --profile illoura-trial` first. Full command reference: [cloud_operations.md](cloud_operations.md).

## Start here: the alarm emailed, or the brief did not arrive

1. `illoura_aws.py status`: shows the instance, services, timers, health checks, last run,
   publication and disk. The failing health check names the problem.
2. `illoura_aws.py watch status`: the external alarm's state and recent checks.
3. Find the row below for the failing check.

Never send or re-send a brief by hand without Sirish's OK, and never re-send an edition that
already has a receipt.

## By symptom

| Symptom (health check / what you see) | Likely cause | What to do |
|---|---|---|
| `daily_brief: missed` and `last_run: source_failure` | A source failed or reported a gap (site down, layout change, blocked) | Read the run folder's `source_refresh.json` (statuses per source) and the source's log under `/opt/aus_tender/logs/`. A quiet day must not fail; if it did, that is a bug to fix. Today is lost by design (no late send after 12:00). |
| `daily_brief: missed`, coordinator `failed` with "Unresolved sends require reconciliation" | An earlier send attempt has no receipt | `illoura_aws.py reconcile-send --run daily_sweep_<time> --yes`. Found in Sent: receipt written, next morning runs normally. Not found (exit 2): check the mailbox; if it truly did not arrive, add `--mark-undelivered`. |
| A source fails with `crawl/field_loss_failed` (after wave 6 is deployed) | Most records of that source came back without a key field: the site's page layout probably changed | Open the source's log under `/opt/aus_tender/logs/` and read the `crawl/field_loss_missing/<field>` counts; fix the selector for that field in the source's spider, then `verify-collectors` and `deploy-collectors --yes`. |
| A stage stopped with a timeout; the brief was retried once (after wave 5 is deployed) | A website or step hung past its time limit | If the retry sent the brief, nothing to do. If not, read `latest_run.json` in `/opt/illoura-routine` for the stage that timed out and that source's log. Never send by hand after 12:00. |
| Brief composed but not sent; output says "timer is not enabled" | The schedule was switched off | Intended? Leave it. If not: `illoura_aws.py schedule enable --yes` (needs Sirish's OK). |
| Compose stopped: "More than half of shortlisted live checks failed" | BuyICT listing pages failing (outage or change) | Check BuyICT in a browser. If it is an outage, wait; if pages changed, fix `routine/scripts/refresh_daily_shortlist.py`. |
| `mail_authorization: failed` | Gmail token revoked or expired (a "Testing" Google app expires tokens after 7 days) | Re-authorise the sender (private workspace: `reports/aws_hosting_trial_20261112/cloud-routine/GMAIL_SETUP.txt`), or publish the Google app ("In production"). |
| `certificate: expiring` / `expired` / `missing` | certbot renewal failing | On the server check `journalctl -u illoura-cert-renew` and port 80 in the Lightsail firewall; `sudo /opt/illoura-certbot/bin/certbot renew --dry-run` tests renewal without changing the certificate. |
| `disk: low` or `retention: attention` | Data growth or a stuck backup | `illoura_aws.py prune --dry-run` to preview; see "Clearing persistent retention attention" in cloud_operations.md. |
| `/readyz` unreachable, `status` cannot connect | Server down or rebooting | Lightsail console: instance state and status checks (the Lightsail alarm also emails). A reboot takes about a minute. |
| Claude Desktop says the `illoura-data` connector failed | Bridge or key problem on that PC | Claude Desktop > Settings > Developer shows the error; log at `%LOCALAPPDATA%\Claude\logs\mcp-server-illoura-data.log`. Re-run the setup kit's `install.ps1` (safe). |
| A team PC or key is lost | Key exposure | `illoura_aws.py api-key revoke --name <name>`; other keys keep working. |

## Routine maintenance

| Task | Command | When |
|---|---|---|
| Security updates + reboot if needed | `illoura_aws.py os-update --reboot --yes` | Monthly or when `status` shows a reboot is required; refuses 05:30-09:30 Sydney |
| Check before any deploy | `verify-ops`, `verify-api`, `verify-collectors`, `verify-routine` | Always; they install nothing |
| Deploy | `deploy-ops`, `deploy-api`, `deploy-collectors`, `deploy-routine` with `--yes` | After tests pass and Sirish agrees; never 06:00-09:00 Sydney |
| Snapshots | `illoura_aws.py snapshots status` | Weekly glance (newest 7 kept, 23:00 UTC) |
| Credits | AWS Settings > Billing, or `aws freetier get-account-plan-state --profile illoura-trial` | Monthly; the Free plan closes the project when credits run out unless upgraded |

## Who decides

Sirish approves pushes, merges, deploys, schedule changes, deletions and any email. Agents
(Codex, Claude) may diagnose and prepare fixes, and record every step in
[workstreams/progress.md](workstreams/progress.md).
