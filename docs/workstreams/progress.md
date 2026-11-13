# Remediation progress (shared by Codex and Claude)

Newest last. Three lines per step: done / next / anything half-finished.
Branch `fix/cloud-stabilization-20261112`; nothing pushed until Sirish approves.
Only one agent edits the branch at a time.

## 12 Nov, Codex: workstreams 1-2
- Done: retention (approved by Claude review); six audited local commits.
- Next: workstream 3.
- Half-finished: none.

## 12 Nov ~23:00 IST, Claude: privacy scrub (Sirish chose fold-into-history)
- Done: real mailbox addresses and a local Windows path removed from code, tests, docs and
  the unpushed history; `git log -p 3756969..HEAD` verified clean; tests 294 + 114 OK.
- Next: workstream 3. Local safety branch `backup/pre-scrub-20261112` must never be pushed.
- Half-finished: none. Server needs `/etc/illoura-ops/health.env` before the next deploy-ops.

## 12 Nov ~23:03 IST, Claude: workstream 3 (Codex at its usage limit)
- Done: `api/` imported (byte-identical to live), `deploy/aws/illoura_aws.py` CLI with 18
  tests, POSIX cloud-init, docs; 39 superseded report helpers archived; live `status` verified.
- Next: workstream 6 (review fixes), then 4 (format-only), then 5 (HANDOFF/history).
- Half-finished: none. Nothing deployed.

## 12 Nov ~23:11 IST, Claude: workstream 6 (review fixes)
- Done: restore attention + exit 1, rollback swap, no-op except removed, per-client failed-key
  throttling (loopback-trusted XFF), bounded/evicting rate maps, CLI verify-ops/verify-api.
  Windows 316/114/20/13 OK; server staged: ops 37 OK, API 13 OK.
- Next: workstream 4 (format-only commit), then 5 (HANDOFF/history).
- Half-finished: none. Not deployed (see 06-review-fixes.md).

## 12 Nov ~23:14 IST, Claude: workstream 4 (lint + format-only)
- Done: lint cleanup commit (unused imports, split imports, `from None`, explicit zip), then
  ruff 0.14 format-only commit on 12 newer files; every file's AST identical before/after;
  tests 318 (17 Linux-only skips) / 114 / API 13 OK.
- Next: workstream 5.
- Half-finished: none.

## 12 Nov ~23:17 IST, Claude: workstream 5 (handoff and history)
- Done: private workspace HANDOFF.md rewritten as a current-state page (<120 lines); dated
  history moved newest-first to a private history file; personal machine notes and the 7 Oct
  reference guide split out; lossless (all 1,333 section lines preserved; original kept).
  Public `docs/history.md` added (sanitized); `docs/HANDOFF.md` marked historical.
- Next: Sirish's decisions: push approval; server deploy (create health.env first); daily
  collection cadence. Then roadmap packages B and C.
- Half-finished: none. Nothing pushed or deployed.

## 12 Nov ~23:45 IST, Claude: push, deploy, daily brief (Sirish: push, deploy, 08:15 email)
- Done: repo confirmed private; branch pushed, PR #1 labelled, workstream tags pushed.
  Server: health setting, deploy-api, deploy-ops, all checks ok; daily brief built (workstream
  7), dry run passed, schedule enabled: first run Fri 13 Nov 06:15 AEDT, email 08:15 AEDT.
- Next: watch the first unattended run and its receipt; then roadmap packages B and C.
- Half-finished: none.
