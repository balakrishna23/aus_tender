# Workstream 6: review fixes

Status: implemented, tested on Windows and on the server (staged, nothing installed);
committed locally. Not deployed, not pushed. Carried out by Claude, 12-13 November 2026.
Order follows the approved plan: review fixes before the format-only commit.

## Fixes

| Review item | Change | Tests |
|---|---|---|
| Restore drill passed with an unresolved send | `restore_drill` returns `status: attention` when any `send_attempt.json` lacks a receipt; the ops CLI exits 1. The coordinator already refused to continue on the unresolved list. | unresolved => attention; resolved => passed; CLI exit 1 |
| Rollback left `previous` stale | `rollback` swaps `current` and `previous` (a second rollback returns to the newer generation), refuses when both name the same generation, validates both pointers, fsyncs the directory. If the second pointer update fails, both pointers name the restored generation and the newer one is still kept by retention. | swap and second rollback; same-generation refusal (Linux) |
| No-op `except BaseException: raise` in `publish` | Removed; body dedented without behaviour change. A comment records that failed staging is retained as evidence. | existing publish/retention suites |
| No backstop for invalid-key floods | Failed authentication (missing, invalid or revoked key) is counted per client; 20 failures in 10 minutes => 429 with `Retry-After`. The client is the `X-Forwarded-For` value only when the request arrives from loopback (nginx overwrites that header with `$remote_addr`); otherwise the socket peer. Malformed or multi-value headers fall back to the peer. | throttling; spoofed header from a non-loopback peer ignored; loopback trust rules |
| Unbounded / stale rate maps | Failure tracking is an ordered map capped at 10,000 clients (oldest evicted). Valid-key rate entries for revoked or removed keys are dropped whenever keys are read. | bounded map; revoked-key eviction |

## Also changed

- `deploy/aws/illoura_aws.py`: `verify-ops` and `verify-api` stage the code and run its
  tests on the server, then remove the staging folder; they install nothing. Test summaries
  are returned as evidence. Fixed the staged ops-test invocation (`tests/` is not a package).

## Verification

- Windows: project tests 316 OK (17 Linux-only skips), local_assistant 114 OK, AWS CLI 20 OK,
  API security 13 OK (Python 3.12, pinned API requirements).
- Server, staged and removed, nothing installed: `verify-ops` "Ran 37 tests ... OK"
  (includes the Linux-only rollback and retention cases); `verify-api` "Ran 13 tests ... OK"
  as `illoura-api`.

## Not done

- Nothing deployed. The live API still runs the 12 November code until `deploy-api --yes`;
  `deploy-ops --yes` additionally needs `/etc/illoura-ops/health.env` first.
- Throttling counts failures per client process; with one uvicorn worker that is global.
