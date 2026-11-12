# Workstream 3: one deployment tool

Status: implemented and tested; committed locally on `fix/cloud-stabilization-20261112`.
Not pushed. Nothing was deployed: the only live use was the read-only `status` action.
Date: 12 November 2026 (Asia/Calcutta). Carried out by Claude while Codex was at its
usage limit, per Sirish's instruction to continue the agreed plan.

## Agreed scope (remediation plan item 3)

Move the AWS helpers into the repository as one CLI (status, deploy-api, deploy-ops,
publish, backup, restore-drill, rollback, prune); ship real files instead of Python
strings run as root; check the first-boot script for the shell Lightsail uses; keep
evidence JSON in the report; archive the one-off scripts.

## What changed

| Path | Change |
|---|---|
| `api/` | API service, Claude bridge and security tests imported from the private report. `app.py`, `claude_bridge.py` and `test_security.py` are byte-identical to the live deployment (app SHA-256 `81da76d1…`). Includes `requirements.txt` and the first-install `install-api.sh` / `enable-https.sh` provisioning records. |
| `deploy/aws/illoura_aws.py` | The CLI. Fixed argument lists only, each checked against a plain-token pattern and shell-quoted; no arbitrary command. SSH host key must be pinned or vouched for by AWS (no trust-on-first-use). |
| `deploy/aws/cloud-init.sh` | First-boot script, now `#!/bin/sh`; `sh -n` passes. Codex's review found the body already POSIX; the 12 Nov failure came from an older wrapper. |
| `requirements-deploy.txt` | `paramiko==4.0.0` for the administrator machine only. |
| `tests/test_aws_cli.py` | 18 tests with a fake server (no network). |
| `docs/cloud_operations.md` | CLI usage table and provisioning notes. |

Added beyond the listed subcommands: `api-key create|revoke`, because the old key helper
is archived and team credentials are still needed.

## Safety decisions

- Production-changing actions (`publish`, `rollback`, `prune`, both deploys) refuse without `--yes`.
- Deploys upload to `/var/tmp/illoura-stage-<time>-<random>` (path-checked), verify every
  SHA-256 on the server, run tests there, then install with fixed owners/modes. A hash
  mismatch or test failure installs nothing; staging is always removed.
- Text files are shipped with LF endings regardless of the Windows checkout.
- `deploy-ops` refuses unless `/etc/illoura-ops/health.env` defines `ILLOURA_GMAIL_SENDER`
  (the privacy scrub moved that value out of source). It compares timer enable states
  before/after and never enables or disables a timer.
- `deploy-api` runs the security tests as `illoura-api`, backs up the live app first and
  restores it automatically if `/healthz` does not recover. Dependency changes are refused
  (they need a reviewed manual install). Keys, environment and snapshot pointers untouched.
- `api-key` writes the key only to the private directory; the server receives its hash.
- No AWS account ID in the repository; optional `ILLOURA_AWS_ACCOUNT` check instead.

## Report-folder housekeeping (private workspace, not in Git)

- 39 superseded one-off helpers moved to `archive-superseded-20261112/`.
- 13 newsletter-routine helpers (and the shared `deploy_live.py`) stay in place: the routine
  depends on private workspace newsletter scripts and is out of this workstream's scope.
- `SCRIPTS_STATUS.md` explains what to use now; `ACCESS_GUIDE.txt` key instructions updated.

## Verification

- `tests/test_aws_cli.py`: 18 passed (shell syntax refused, managed backup paths only,
  `--yes` gates, health-sender refusal, hash mismatch and failed tests install nothing,
  fixed install destinations, timer change detected, no timer enable/disable, dependency
  change refused, CRLF-only difference accepted, API tests run as the API user, backup
  before install, unhealthy API rolled back, key never uploaded).
- `api/test_security.py` from its new location: 9 passed (pinned API requirements, Python 3.12).
- Full Windows suites after the change: see the commit record in `progress.md`.
- Live read-only `status`: instance running, SSH limited to the administrator /32,
  API and nginx active, collection timer disabled, health/cert timers enabled, health ok,
  generation `generation-20261112T154144Z-67480bd7` published, 150.8 GB free.

## Not done / next

- The newsletter routine is not in the repository (private dependencies).
- No deployment was made. Before the next `deploy-ops`, create `/etc/illoura-ops/health.env`.
- Workstream 6 (review fixes, including API failed-key throttling) now has a canonical
  `api/app.py` to change.
