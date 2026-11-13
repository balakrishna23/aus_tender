# Workstream 8: first-run failure, alerts and off-server snapshots (13 November 2026)

Branch `fix/collectors-and-alerts-20261113` (stacked on PR #1). Done by Claude.

## What happened on 13 November

The first unattended daily brief (06:15 Sydney) stopped after 11 minutes with
`source_failure`. Seven sources were OK; `aus_contract` reported `FAIL(no items)`.
Its log showed 13 new notices to fetch: all 13 redirected (HTTP 302) to AusTender's
`/System/NotFound` page, so their pages were not on the site yet. One reached the
parser ("No page on the site for CN3594157-A1"); the other 12 were dropped by the
duplicate filter because they redirected to the same URL. Nothing was saved, the
runner treated that as a failed source, publication and the 08:15 email were skipped
by design, and nothing told anyone.

## Changes

| Change | Where | Verified |
|---|---|---|
| Every notice request uses `dont_filter`, so each unpublished notice is counted. `crawl/nothing_due` when the API has nothing new, or when every new notice is unpublished (at most 25; more fails loudly as a likely site change). Retried by later runs. | `spiders/aus_contract.py`, `tests/test_collectors_unpublished.py` (9 tests, including the exact 13 November stats) | Windows 9/9; server staged 9/9; deployed 05:30 UTC |
| `deploy-collectors` / `verify-collectors`: ship only `run_spiders.py` and `spiders/*.py`, hash-check, run collector tests on the server against the staged code, refuse while the brief or collection runs, back up and install only changed files as `scraper`. | `deploy/aws/illoura_aws.py`, `tests/test_aws_cli.py` | The first deploy also normalised four unchanged spiders from CRLF to LF (identical ignoring line endings; originals in `/var/backups/illoura/collectors-20261113T053009Z`). |
| Health: a run within 5 hours is `in_progress` (healthy), so `/readyz` no longer turns 503 during every morning collection. New `daily_brief` check: while the timer is enabled, a send receipt dated that Sydney day must exist by 09:00. | `deploy/illoura_cloud_health.py`, `tests/test_cloud_health.py` (7 new tests) | Server staged ops 55/55; deployed with `deploy-ops` |
| External watch: CloudFormation stack `illoura-watch` (Lambda every 5 minutes, alarm on 30 minutes of failure or missing checks, SNS email on alarm and recovery). | `deploy/aws/illoura-watch.yaml`, `watch deploy/status` | cfn-lint clean; stack `CREATE_COMPLETE`; manual invocation reached the server (503 while the failed run is current) |
| Daily Lightsail automatic snapshots at 23:00 UTC (newest 7 kept). | `snapshots enable/status` | Add-on enabling; first snapshot 13 November 23:00 UTC |

Windows: 361 project tests OK (17 Linux-only skips).

## Still open

- The SNS email subscription must be confirmed from the AWS email before alarms arrive.
- 13 November: the data served is from 12 November; the next run is 06:15 Sydney on
  14 November. The watch alarm stays in ALARM until that run publishes and sends.
- Whole-VM restore from a snapshot has not been rehearsed.
- On 13 November the `daily_brief` check reads `ok` because the 12 November manual
  edition was sent at 02:17 Sydney on 13 November; from 14 November sends are at 08:15.
