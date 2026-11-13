# Workstream 2: audited local commits

Date: 12 November 2026 (Asia/Calcutta).
Branch: `fix/cloud-stabilization-20261112`.
Status: local checkpoints created; validation passed; awaiting source-diff review.
Nothing pushed.

## Scope and boundaries

The user approved retention after Claude's source review and authorized logical
commits on this branch. The three review notes are in `docs/cloud_operations.md`:
publication can succeed with exit 1, damaged backups require inspection and
quarantine, and crash-leftover temporary pointer links are intentionally retained.
No approved retention implementation changed for those notes.

No timer activation, deployment, production pruning, collection, migration,
newsletter or outreach is authorized by this workstream. No GitHub push is made.
Formatting remains a separate later workstream; these are source checkpoints.

## Audit method and dispositions

Inventory every modified tracked file and every new source file, including the
otherwise ignored `bdm_workbench.html`. Scan full contents before staging for key
material, provider tokens, credential-bearing URLs, long credential assignments,
email literals and prohibited data paths. Repeat the scan on Git's staged blobs
before each commit; record their hashes and review each staged file list and diff.
Scan output contains locations and classifications, never credential values.
Run `git diff --cached --check` before committing.
The check identified existing extra EOF blank lines in three new cloud service/
timer files. They were preserved and recorded for the separate formatting commit;
no other staged whitespace findings were accepted.

- Credentials, OAuth client/token JSON, private SSH keys, `.env`, inputs,
  databases, exports, runtime files and generated reports stay outside commits.
- No credential/key indicators were found in the initial 48-file inventory.
- Email indicators are present: synthetic test fixtures, existing README text,
  internal sender/recipient configuration and a generated message-ID domain.
  These are not a harvested contact dataset. Fixed internal identities remain
  in this private local checkpoint and must be reviewed before any public push;
  configuration consolidation is workstream 3. This is not public-release clearance.
- The preview PowerShell launcher contains a machine-specific Python path.
  It is source configuration, not a credential; portability remains open.
- The HTML workbench is application source, not a generated contact dashboard.
  Add only its exact `.gitignore` exception, retaining the global HTML exclusion.
- `supabase_migrate.py` is a dormant legacy bulk-transfer utility. Its source
  references confidential data locations but contains no data or connection
  credentials; connection configuration comes from `.env`. Checkpoint source
  separately, never execute it. Operational approval is still required before use.
- API and Claude bridge source are still in the private trial report directory.
  Their canonical import and API commit belong to workstream 3; no empty or
  misleading API commit is created here.

## Commit grouping

1. Cloud operations, approved retention, service definitions, tests and runbook.
2. Procurement data quality, source-status and contact-evidence corrections.
3. BDM monitor, authorization/workbench/workflow source, examples and tests.
4. Preserve the user's local-AI disable marker and startup guards.
5. Legacy migration utility source only.
6. README and this audit record.

Store the exact base/head IDs, per-commit patches, aggregate diff, file inventory,
staged scan results and validation logs in the private review reports. Include
copies of review patches under ignored `tmp/commit-review` for Claude in VS Code.
Do not put private evidence JSON or generated test output into the repository.

## Validation and review

The code checkpoint tested was `4bd2c128aac56cff9d4ce206c3417d3e446d2353`.
It was exported with `git archive`; tests used that export, not the populated
working directory. The subsequent documentation commit changes no tested source.

| Suite | Windows | Isolated server fixture |
| --- | --- | --- |
| `python -m unittest discover -s tests` | 269 passed, 15 skipped | 284 passed |
| `python -m unittest discover -s local_assistant -p 'test_*.py'` | 114 passed | 114 passed |
| Unchanged API `python -m unittest test_security` | 9 passed | 9 passed |

Windows used the existing Python environment; API dependencies were supplied by
`uv run --with-requirements`. Linux used existing collector/API virtual environments
against a new `/var/tmp/illoura-commit-review-*` fixture. API tests used copied,
unchanged API source outside this Git tree, with synthetic authentication.
No production source tree, service, database or timer was modified. The local
server-test helper initially lacked Paramiko; rerunning it with `uv run --with
paramiko` supplied the SSH dependency without changing project requirements.

The repeated ten-day Linux retention simulation stayed below its 2,097,152-byte
budget: peak 996,337 bytes; final 812,887 bytes; five generations and fourteen
backups retained. These are synthetic managed-directory measurements, not a cap
on production disk use. Existing production-size estimates are in workstream 1.

Local code commits:

(Listed by subject: the privacy rewrite below changed every hash; see `git log`.)

- "Checkpoint cloud operations and approved retention": cloud operations, retention and runbook notes.
- "Correct procurement status and identity evidence handling".
- "Checkpoint internal BDM monitor and working view".
- "Preserve local AI disable marker and startup guards".
- "Checkpoint dormant legacy migration utility source".

The final documentation commit records this audit and the pending README changes.
Actual patches and the complete commit manifest accompany the review handoff under
ignored `tmp/commit-review`. No patch is a deployment authorization. Workstream 3
remains pending the user's Claude review; its API import, configuration/CLI work,
later formatting, handoff rewrite and review fixes have not been folded into this
checkpoint. No GitHub push, collection timer activation or mail send occurred.

## Privacy scrub before push (Claude, 12 November 2026)

Claude's pre-push review found real internal mailbox addresses hard-coded as the
BDM sender/recipient defaults, in the example config and docs, and as the expected
Gmail identity in the cloud health check, plus a machine-specific Windows user path
in `deploy/run-bdm-preview.ps1`. The repository is public, so Sirish approved
folding the fixes into the unpushed commits (the content never reaches history).

- `bdm_monitor.py`: no address defaults. Sender/recipient come from the private
  config or `ILLOURA_MAIL_SENDER` / `ILLOURA_MAIL_RECIPIENT`. The internal-only guard
  is kept as a generic rule: exactly one plain recipient on the sender's own domain;
  delivery re-checks it. Unconfigured previews still run.
- `deploy/illoura_cloud_health.py`: expected identity from `ILLOURA_GMAIL_SENDER`
  (service `EnvironmentFile=-/etc/illoura-ops/health.env`); missing => `unconfigured`
  attention, not a crash. Must be configured before this version is deployed
  (see docs/cloud_operations.md). Nothing was deployed.
- Example config/docs use `example.com` placeholders and generic wording; the preview
  script uses `$env:LOCALAPPDATA` or `ILLOURA_BDM_PYTHON`.
- Tests: 10 added (mailbox guard, unconfigured health). Windows: tests 294 OK
  (15 Linux-only skips), local_assistant 114 OK.
- Method: `backup/pre-scrub-20261112` (local only, never push) -> fixup commits ->
  non-interactive autosquash rebase onto 3756969. Verified afterwards that
  `git log -p 3756969..HEAD` contains none of the removed values.
- Local private config `inputs/bdm/monitor.yaml` (git-ignored) already holds the real
  pair, so the local monitor keeps working unchanged.
