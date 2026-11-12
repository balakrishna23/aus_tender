# Workstream 7: scheduled daily brief (08:15 Sydney)

Requested by Sirish on 12 November 2026 ("collection to run and send the email at 8:15 am
Australian time"), after approving push and deployment. Built, deployed and enabled by
Claude the same night. Usage: `docs/cloud_operations.md`, section "Daily brief".

## Design

- `deploy/illoura_daily_brief.py`: `run` (timer, root) collects and publishes through the
  existing coordinator, then composes and sends as `scraper`; `compose`, `send`, `dry-run`.
- `deploy/illoura-daily-brief.service` / `.timer`: 06:15 Australia/Sydney, no catch-up,
  6-hour limit, health service on failure. The old collection timer stays disabled.
- Before this, every edition had a hand-written editorial file and a manual review record.
  Automatic editorial: the email states the selection was automatic; listing facts come
  from the composer's live check (closing time, location, work mode, clearance, candidate
  cap, term, Q&A status); a warning is added when listing text suggests invited sellers only.
- Selection parses `closes_at` with its offset (text comparison of mixed offsets is wrong
  by up to 11 hours; covered by a test).
- Sending reuses the existing helper unchanged: source-gap refusal, edition date, content
  hashes, Gmail identity and Sent-folder duplicate check. Past 12:00 Sydney the day is skipped.
- Operations CLI: `set-health-sender`, `daily-brief-dry-run`, `schedule enable|disable`.
  `deploy-ops` treats a newly installed timer ("not-found" to "disabled") as no change.

## Deployment record (12 November 2026, times IST)

- Private health setting written; staged server tests: operations 48 OK, API 13 OK.
- `deploy-api`: throttling version live, previous app backed up, `/healthz` and `/readyz` OK,
  Claude bridge returns 429,833 contracts. `deploy-ops`: operations, retention, health,
  daily brief installed; timers unchanged until the schedule was enabled.
- Health: all checks ok including `mail_authorization`. `prune --dry-run`: two generations
  protected, nothing to delete.
- Dry run on a copy of the latest run: 8 requests selected, all passed the live check,
  composed, layout checks passed, nothing sent.
- `schedule enable`: next run Friday 13 November 06:15 AEDT; email at 08:15 AEDT.

## Tests

- `tests/test_cloud_daily_brief.py` (11): window and ordering, offset parsing, labour-hire
  preference and fill, malformed times, new-since-baseline, composer fields, access warning,
  source gaps, U+FFFD cleaning, still-open rule, 08:15 window and noon cutoff.
- `tests/test_aws_cli.py` (24): adds health-sender upload/validation, schedule gating,
  daily brief shipped, new-timer handling.
- Windows full suite 332 OK (17 Linux-only skips); server staged operations suite 48 OK.

## Limits

- No human editorial judgement: ranking is "closing soonest", not fit to Illoura's bench.
- A source gap on the day means no email (by design of the send helper).
- The first unattended run is Friday 13 November; check its receipt and screenshots.
