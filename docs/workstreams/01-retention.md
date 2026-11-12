# Workstream 1: retention

Status: approved by the user following Claude code review; not deployed.
Date: 12 November 2026 (Asia/Calcutta).
Branch: `fix/cloud-stabilization-20261112`. No commits or pushes existed at the
original workstream-1 review handoff; audited checkpoint commits are workstream 2.

## Authorization and boundaries

- Implement the approved retention policy first.
- Keep current, previous, and the three newest other complete generations.
- Delete failed `.generation-*` directories only when older than two days.
- Retain fourteen complete operations backups.
- Prune after successful publication while existing write locks remain held.
- Report deletion/reclaimed-space information in operational health.
- The API request path stays unchanged. No reader lock or grace period is added.
- No timer activation, production pruning, or production deployment during review.
- Existing unrelated local changes are preserved.
- User requested an actual diff after each workstream and detailed documentation.

## Initial inspection

The existing publisher retained every generation and failed staging directory.
Rollback and restore behavior have separate review fixes scheduled for a later
workstream; this change does not mix those fixes into retention.

The Git working tree already contained modified and untracked application files.
An exact pre-workstream copy of each affected existing file was saved privately
under the dated workspace report directory, `retention-review/before/`. This makes
the workstream diff distinguishable from earlier uncommitted work.

Preflight commands:

```text
git status --short
git branch --show-current
git switch -c fix/cloud-stabilization-20261112
python -m unittest discover -s tests
python -m unittest discover -s local_assistant -p test_*.py
```

Baseline suites run on Windows with Sheets auto-sync disabled. Their detailed logs
remain in the private workspace report directory; results will be recorded below
after completion. No local AI/model downloads are authorized or required.

## Files changed

| File | Change |
|---|---|
| `deploy/illoura_cloud_ops.py` | Policy, safe pruning, dry-run CLI, post-publication cleanup result and restore/prune lock coordination. |
| `deploy/illoura_cloud_collect.py` | Pass backup root, record retention result and preserve published-with-cleanup-attention state. |
| `deploy/illoura_cloud_health.py` | Report latest reclaimed bytes, observed free-space change, retained bytes and cleanup errors. |
| `tests/test_cloud_retention.py` | New retention, safety, failure, four-publish reader and ten-day budget tests. |
| `tests/test_cloud_health.py` | Reclaimed-space reporting and attention-state coverage. |
| `docs/cloud_operations.md` | Distinguish review-only implementation from deployed behavior. |
| `docs/workstreams/01-retention.md` | This execution and review record. |

## Policy and safety decisions

- Resolve `current` and `previous` under the existing write locks. Broken or escaping
  pointers refuse pruning; absent `previous` is allowed for a first generation.
- Always protect both targets, irrespective of their timestamps. Keep the newest
  three other complete generations using completion timestamps, with name as a
  deterministic tie breaker. Legacy `snapshot-*` directories count toward the policy.
- Treat two pointers to the same generation as one retained physical directory.
- Validate absolute deletion paths immediately before recursive deletion. Reject
  symlink/junction candidates, escaping targets and overlapping managed roots.
- Failed `.generation-*` staging expires only when age is strictly greater than
  172,800 seconds. Supported concurrent writers are excluded by the existing locks.
- Validate backup completion manifests, safe file paths and checksums. Retain the
  fourteen newest valid copies. Corrupt/incomplete copies remain for manual review
  and raise attention rather than displacing a good backup.
- Run cleanup after the successful atomic publication switch. A cleanup failure
  sets `published=true` and `retention_attention=true`, preserving the valid generation.
- The coordinator records cleanup attention; a standalone CLI returns nonzero for
  attention. `prune --dry-run` neither deletes nor writes the retention report.
- Restore through the CLI now holds the same write locks, preventing an old backup
  from being pruned while that CLI is reading it. Direct library callers must hold
  the same locks; the coordinator already does.
- Report logical regular-file bytes removed separately from observed filesystem
  free-space change. Compression, shared blocks and unrelated writes can make them
  different. Health exposes both values and the latest deletion/error counts.
- API source and request processing are unchanged. A Linux integration test pins
  the initial generation, performs four further real publications, and verifies
  the old manifest, permissions and SQLite contents remain available throughout.

## Execution record

1. Created the branch without resetting, staging or committing existing local work.
2. Saved pre-workstream copies and the API source hash outside the Git repository.
3. Windows baseline: project 263 tests passed, three Linux-only skips; assistant
   114 passed. Sheets synchronization disabled for tests.
4. Implemented pruning and coordinator/health integration, then added disposable
   filesystem fixtures. No test uses production databases or source contact records.
5. First paired run: Windows project 280 passed; Linux project had seven errors
   because the source-only test archive omitted `templates/outreach_email.txt`.
   The archive manifest was corrected; application/outreach code was not changed.
6. Next paired run: project 282 passed on both platforms; assistant/API suites passed.
7. Adding checksum validation introduced a mistaken list/dictionary loop edit.
   Tests caught it: Windows project had three errors and Linux eleven. Corrected
   both loop locations, reran focused checks, then full checks passed (283 project).
8. Added the explicit four-subsequent-publish reader test and explicit cleanup-
   attention flag. Final full-suite results are recorded below.
9. Early harness runs replaced their latest log files; per-attempt log archival was
   subsequently added. Later attempts are preserved in the private review folder.
10. Server tests execute from a unique `/var/tmp/illoura-retention-review-*` source
    fixture. Project/assistant tests run unprivileged; API tests run as `illoura-api`
    with temporary test credentials. No production code installation or pruning occurs.

Reproducible test commands:

```text
python -m unittest discover -s tests -v
python -m unittest discover -s local_assistant -p test_*.py -v
python -m unittest test_security -v
```

The API command runs from the unchanged API source directory, using its pinned
requirements. Windows uses Python 3.11; the existing Linux virtual environments use
Python 3.12. No model runtime was installed, started or contacted.

## Final verification

Final result counts and the diff manifest are recorded in the private review bundle
and summarized in the review handoff. Linux exercises actual symlinks and pruning;
Windows skips the Linux-only filesystem cases, while portable selection/backup and
safety tests run there. Server timer state is compared before/after tests: collection
disabled, health enabled and certificate renewal enabled, unchanged.

| Final suite | Windows | Existing Linux server, isolated fixture |
|---|---|---|
| Project `tests/` | 284 passed, including 15 explicit Linux-only skips | 284 passed, no skips |
| Assistant `local_assistant/test_*.py` | 114 passed | 114 passed |
| Unchanged API/security suite | 9 passed | 9 passed |

The first row's Windows count is the unittest total: 269 executed successfully
and 15 skipped. No failed tests remain. Source syntax/backspace checks and
`git diff --check` also pass. Existing line-ending conversion notices are not test
failures. The final server source archive hash and per-file source hashes are in
the review bundle so the tested source can be compared with the reviewed diff.

## Disk-budget evidence

The ten-day simulation uses real temporary SQLite files with a fixed 32,768-byte
payload, two complete operations backups per day (twenty created), daily failed
staging and one deliberately failed publication. It measures both retained and
pre-pruning peak bytes across the generation and operations-backup roots.

Declared fixture ceiling: **2,097,152 bytes (2 MiB)**.
Measured peak: **996,337 bytes**.
Final retained: **812,887 bytes**, with five complete generations and fourteen
complete operations backups. Daily measurements are saved in `server_disk-budget.json`.

These are scaled fixture measurements, not a claim that production fits in 2 MiB.
Read-only production reference inspection measured:

- Current generation: 3,133,180,953 bytes.
- Five same-size retained generations: 15,665,904,765 bytes.
- Six generations including an in-flight publication: 18,799,085,718 bytes.
- Largest existing operations backup: 9,766,820 bytes; fourteen at that size would
  require 136,735,480 bytes.

Those extrapolations exclude the writable source databases, OS, logs/reports,
recent failed staging and future data growth. The pre-publication disk-headroom
check remains in place. Retention counts are not an unlimited-growth byte guarantee.

## Review artifacts and remaining boundaries

- Actual unified diff: `tmp/retention-review/01-retention.diff` (ignored review artifact).
- Validation summary, source hashes and budget JSON: `tmp/retention-review/`.
- Complete detailed logs and preserved attempt logs: the dated private workspace
  report directory, `retention-review/`.
- This is a diff against the saved pre-workstream source, including changes to
  files that were already untracked; it is not a misleading empty Git diff.
- API source hash must match the saved pre-workstream hash and the live API hash.
- Corrupt/incomplete directories are deliberately retained and flagged. They require
  inspection rather than an invented deletion rule. Persistent I/O/permission failures
  or partial cleanup can require operator repair before another successful prune.
- No production pruning/deployment, timer activation, collector run, email send,
  local commit or GitHub push occurred during workstream 1. The user subsequently
  approved the code and requested audited local commits in workstream 2.
- Restore attention status, rollback behavior, API throttling, CLI consolidation,
  formatting and handoff restructuring remain in their agreed later workstreams.

## Review disposition

The user reported Claude's approval on 12 November 2026. Three runbook-only notes
were accepted: publish's exit-1 cleanup-attention semantics, quarantining corrupt/
incomplete backups without destroying mail-state evidence, and harmless temporary
pointer links that are intentionally outside pruning. These notes were added to
`docs/cloud_operations.md`. No approved retention code changed in response.
