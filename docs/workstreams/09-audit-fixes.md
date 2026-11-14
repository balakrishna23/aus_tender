# Workstream 9: fixes from the 13 November failure audit

Owner split: Claude (server settings, operations CLI, health, docs, deploys) and Codex
(daily brief send safety, restore drill), in separate git worktrees so nobody edited the
same file. Codex in VS Code reviewed read-only. Started 13 Nov 2026 ~12:45 IST at
Sirish's request ("distribute the work and start working on auto mode").

## Why
A read-only audit on 13 November (Claude: server, AWS, this PC; Codex: code) found these
risks to the 08:15 Sydney email. Evidence and the full list are in the private workspace
(`reports/server_audit_20261113/combined_findings.md`).

| # | Risk (verified) | Fix |
|---|---|---|
| 1 | Ubuntu installs security updates at a random time 06:00-07:00 Sydney and needrestart then restarts affected services automatically. The brief runs 06:15-08:15, so a library update could kill and restart it. A glibc update was queued for 06:13 on 14 Nov. | `harden-os`: needrestart never restarts `illoura-(daily-brief\|cloud-collect\|cloud-health).service`; updates at 03:30-04:00 Sydney. Pending updates installed and the server rebooted onto the new kernel the same evening (`os-update --reboot`). |
| 2 | A Gmail send that fails or times out leaves an attempt without a receipt; every later morning stops at the restore drill until someone fixes it by hand. | `illoura_daily_brief.py reconcile` + CLI `reconcile-send` (operator-run): look the message up in Sent; write the receipt, or record an operator's "not delivered" decision; the restore drill accepts that. Never sends. Wave 3: the morning coordinator runs the Sent lookup automatically first; only confirmed sends are settled without a person. |
| 3 | A same-day rerun could send twice (duplicate check per folder + subject, and the subject contains the request count). | One send per edition date across all run folders, under a server-wide `send.lock`. |
| 6 | `schedule disable` did not stop a brief already waiting for 08:15. | The CLI also stops the running service; the brief re-checks the timer and the noon cutoff just before sending. |
| 9 | A failing certificate renewal was only noticed when the 6-day certificate expired. | Health `certificate` check: under 24 hours left -> attention -> `/readyz` 503 -> external alarm. |
| 27 | A BuyICT outage produced an email saying "0 requests to check". | Compose refuses approval when more than half of the live listing checks fail. |

Also cleared the two stale failed units (cloud-init's launch script, 12 Nov; the skipped
13 Nov brief).

## Wave 1 deployed 13 Nov (PR #3)
All rows above, plus the external alarm now fires when most checks fail (Average < 0.5 over
two 15-minute periods) instead of only when all fail (audit #32). Checks: Windows 402 tests,
server ops tests 88, health OK incl. certificate, reconcile-send on two safe runs, dry run.

## Wave 2 built 13 Nov, deploy after the 14 Nov 08:15 send (PR #4, stacked on #3)
- aus_contract canary replaces the 25-notice cap; unpublished notices retried from a
  `pending_notices` table (30-day give-up); quiet ATM days are not FAIL(no items).
- `verify-collectors` / `deploy-collectors` stage every `tests/test_collectors_*.py`.
- Server staged collector tests 28 OK; canary checked read-only on real data.

## Waves 3 and 4 built 13 Nov (PR #5), deploy in two steps
After the 14 Nov 08:15 send (with wave 2): `deploy-ops` for
- Health: a missing certificate is "missing" (attention) instead of a skipped check (Codex
  review of PR #3).
- The morning coordinator settles uncertain sends that Gmail Sent confirms before
  collecting; "not found" still needs `reconcile-send` (operator).

After Sirish's pilot-week review (about 20 Nov):
- `routine/` in the repo (server copies of 13 Nov; mail addresses and link id moved to the
  private `/etc/illoura-routine/mail.env`, created 13 Nov by `set-mail-config`), failed
  supplemental research listed in the coverage note; `verify-routine` 25 OK on the server;
  install with `deploy-routine --yes`.
- Collectors fail loudly instead of saving silently wrong data (Codex audit #7, #9, #19,
  #22): contract API envelope check; NSW closes absent tenders only after a complete walk;
  NSW notice page cap reported as PARTIAL; ACT explicit empty lists are a quiet day.
  `deploy-collectors` now also ships `austender_api.py` and `nsw_tender_fetch.py`;
  `verify-collectors` 47 OK on the server. Check the live ACT empty-state wording first.

Server housekeeping 13 Nov (Sirish: delete if not worth keeping): removed 471 MB of
leftovers (the 12 Nov data upload, duplicated on the server, this PC and the 12 Nov
Lightsail snapshot; install kits; staging copies; /var/tmp review and dry-run folders).
Small files were archived first in the private workspace (`reports/server_leftovers_20261113/`).

## Waves 5 and 6 (merged 13 Nov as PRs #6 and #8, NOT deployed)
Built by Codex in its own worktree, reviewed and server-tested by Claude (staged tests:
operations 102, routine 28, collectors 73 OK). Deploy after the pilot-week review.
- Wave 5, time limits and one retry: `routine/run_once.py` gives each stage a deadline
  (spider 45 min, sources 60, research 60, finalization 45, whole routine 105) and kills
  the whole process tree on timeout. `deploy/illoura_daily_brief.py` retries a failed
  collection or composition once after 5 minutes, only if it starts by 09:30 Sydney and
  can finish before the noon cutoff. The send and the unresolved-sends guard are never
  retried. Check on the first real run: the process-tree kill and that the deadlines reach
  the routine through `runuser`.
- Wave 6, remaining collector checks (Codex audit #18, #20, #21, #25, #26): NSW details of
  known open tenders are refreshed after 7 days (20 a run); planned procurement flags
  plans that come back empty without the site saying so; ACT compares rows with the
  stated total; BuyICT follows the fallback paging and fails on an unexplained stop;
  `pipelines.py` fails a source (`crawl/field_loss_failed`) when 10 or more items were
  collected and over half lack a key field. On the server 0 of the last 500 stored rows
  lack one. `deploy-collectors` also ships `pipelines.py`. Check on the live sites first:
  the empty-list wording of ACT and planned procurement, and BuyICT's fallback paging
  field names (tested against sample data only).

## False alarm of 14 November (fixed in code, deploy pending)
The 14 Nov brief was sent at 08:15 Sydney, yet the outside alarm emailed at 08:47 and cleared at
09:17. Cause: between the send and 09:00 the health check still expected a receipt dated the
previous day, so today's receipt read as `daily_brief: missed`. It would repeat every morning.
Fix: a receipt dated on or after the due day counts (`deploy/illoura_cloud_health.py`, test in
`tests/test_cloud_health.py`). Install with `deploy-ops --yes`.

## False-alarm review of 14 November
After the 08:47 false alarm every path to the alarm was reviewed. Fixed in `deploy/illoura_cloud_health.py`:
- Brief sent today read as missed until 09:00 (PR #9).
- A late morning (retry, slow source) alarmed at 09:00 although the brief could still go out: now `pending`
  while `illoura-daily-brief.service` is active, until the 12:00 cutoff.
- The same late morning made the publication read as stale after 26 h: now `in_progress` up to 32 h while
  the service works.
- Two failed calls to Google in a row read as lost Gmail authorisation: network and Google-side errors are
  tolerated for 6 h after the last confirmed check; a revoked or expired grant still fails at once.

Reviewed and left as they are: the outside watcher (needs 30 minutes of mostly failing checks, so reboots and
deploys do not alarm), the Lightsail status alarm (two 5-minute periods), certificate, disk and retention checks.

## Cross testing, 14 November
Sirish asked Claude and Codex to test the whole project against each other. Each wrote tests
for the other's code in its own worktree, then Codex attacked the fixes in a second round and
picked three areas nobody had tested. Failing tests were kept failing until the code was fixed.
- Round 1: Claude wrote 45 tests for Codex's code (brief, routine, canary, retention); 12 failed.
  Codex wrote 51 for Claude's code (operator tool, health, API, send recovery, mail, watcher); 15 failed.
- Round 2: Codex wrote 47 more; 19 failed (send-state matrix, API header and key shapes, database
  identity on save, runner persistence and sync classification, rollback verification).
- All fixed; 672 tests pass on Windows; staged server suites pass (ops 115, API 15, collectors 73,
  routine 28). The retention policy held over 80 generated layouts without a fix.
- Highest-impact fixes: a damaged or "not sent" receipt no longer counts as a send; a delivered
  brief is recognised whatever the address case; one odd listing or damaged old run folder no longer
  stops the brief; a failed API restart rolls back; a page URL cannot overwrite another notice; items
  that were not saved fail the source; one withdrawn notice no longer fails a quiet day.
- Not deployed: these changes sit on main with waves 4-6 and go live with them after the pilot review.
- Not covered: real web-framework transport beyond the two API tests, live sites, real Gmail and AWS,
  power loss during publication. Cross tests for collectors run in CI, not in the staged server suite.

## Not done yet
- Run-report retention (about 5 MB a day; reports are send evidence, keep for now).
- Gmail OAuth app: Sirish keeps it in pilot/test for a week and confirms later. If it is
  "Testing", the token authorised 12 Nov about 15:16 UTC expires about 19 Nov 15:16 UTC
  (20:46 IST): the 19 Nov brief still sends, the 20 Nov one would fail and the alarm emails.
