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

The authenticated `/v1/operations` response includes `checks.last_run.sources`,
`failed_sources` and `missing_sources`. These name source failures without exposing logs,
contacts or server paths. An unreadable source report is shown as `source_report: unavailable`.

### Access from another computer

Lightsail browser SSH is enabled through the restricted `lightsail-connect` firewall alias.
The existing administrator address rule is retained. Sign into the AWS Management Console,
select Sydney (`ap-southeast-2`), open Lightsail > `illoura-hosting-trial` > Connect >
Connect using SSH. This uses the console's authenticated connection and needs neither this
PC's private key nor its current public address. The firewall configuration was verified;
an interactive sign-in from a second device has not been tested.

In that private browser terminal, inspect health and the morning job:

```sh
sudo cat /var/lib/illoura-health/status.json
sudo systemctl status illoura-daily-brief.service --no-pager
sudo journalctl -u illoura-daily-brief.service -n 80 --no-pager
```

Do not enable timers, run a send, or paste credentials into that terminal as part of diagnosis.

Never send or re-send a brief by hand without Sirish's OK, and never re-send an edition that
already has a receipt.

## By symptom

| Symptom (health check / what you see) | Likely cause | What to do |
|---|---|---|
| `daily_brief: missed` and `last_run: source_failure` | BuyICT failed or reported a gap, or the collection was cut short or incomplete (a problem in another source alone no longer stops the day: it is named in the brief and in `last_run.source_gaps`) | Read the run folder's `source_refresh.json` (statuses per source) and the source's log under `/opt/aus_tender/logs/`. A quiet day must not fail; if it did, that is a bug to fix. Today is lost by design (no late send after 12:00). |
| `daily_brief: missed`, coordinator `failed` with "Unresolved sends require reconciliation" | An earlier send attempt has no receipt | `illoura_aws.py reconcile-send --run daily_sweep_<time> --yes`. Found in Sent: receipt written, next morning runs normally. Not found (exit 2): check the mailbox; if it truly did not arrive, add `--mark-undelivered`. |
| A source fails with `crawl/field_loss_failed` | Most checked rows lost a key field that was previously present or have no stored baseline | Read `crawl/field_loss_missing/<field>` in the source log. A matching omission in the same stored URL is counted separately as `crawl/field_loss_known_omissions`; it does not conceal a newly missing value. Check the live source before any collector deployment. |
| A stage stopped with a timeout | A website or step hung past its time limit | Collection retries wait 5, 20, 40 and 60 minutes; composition retries once after 5 minutes. The latest retry start is 09:30 Sydney and the cutoff is noon. If retries fail, inspect `latest_run.json` and that source's log. Never send by hand after noon. |
| `reconcile-send` prints `attempt_unreadable` (exit 2) | The send was stopped while writing its attempt record, so Gmail cannot be searched | Check the team mailbox for that morning's brief. If it did not arrive: `illoura_aws.py reconcile-send --run daily_sweep_<time> --mark-undelivered --yes`. If it did, leave it and tell Claude (the receipt must be written by hand). |
| Brief composed but not sent; output says "timer is not enabled" | The schedule was switched off | Intended? Leave it. If not: `illoura_aws.py schedule enable --yes` (needs Sirish's OK). |
| Compose stopped: "More than half of shortlisted live checks failed" | BuyICT listing pages failing (outage or change) | Check BuyICT in a browser. If it is an outage, wait; if pages changed, fix `routine/scripts/refresh_daily_shortlist.py`. |
| `daily_brief: ok` with `phase: pending`, or `publication` with `phase: in_progress` | The morning run is late (a retry or a slow source) but still working | Nothing to do. If the brief is not sent by 12:00 Sydney the status becomes `missed` and the alarm emails. |
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
