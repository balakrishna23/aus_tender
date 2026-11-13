# Workstream 9: fixes from the 13 November failure audit

Owner split: Claude (server settings, operations CLI, health, docs, deploys) and Codex
(daily brief send safety, restore drill), in separate git worktrees so nobody edited the
same file. Codex in VS Code reviewed read-only. Started 13 Nov 2026 ~13:30 IST at
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

## Not done yet (next wave)
- Per-stage deadlines and one bounded retry before noon: need the newsletter routine
  scripts in the repository first (9 differ between the server and the local copies).
- Retention for run reports and dry-run copies; a 419 MB leftover upload in /home/ubuntu
  (deletion needs Sirish's OK).
- Collector silent-output risks from the audit (NSW closing logic, pagination checks,
  contract API envelope validation, field-loss checks).
- Gmail OAuth app: Sirish to confirm it is Internal or In production (a "Testing" app's
  refresh token expires after 7 days).
