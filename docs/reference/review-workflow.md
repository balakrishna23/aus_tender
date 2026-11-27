# Reference: internal research review

Files:

- `api/review_page.py`: self-contained `/review` interface; sessionStorage key,
  three task tabs, source links, progress, escaped DOM text, same-origin fetch.
- `api/review_support.py`: standard-library wire validation, contact-free fixed
  column allowlists and canonical evidence fingerprint shared by API and importer.
- `api/app.py`: review-scoped queue, submission and per-reviewer status endpoints.
- `market_intelligence/review_inbox.py`: offline importer and private env-file entry
  point; existing review/watchlist/profile functions execute inside the validation
  transaction, with advisory and row locks. No migration or new published table.
- `deploy/illoura_review_import.py`: root scheduling boundary; switches to `ubuntu`,
  takes the existing research lock, bounds process-group lifetime, defers during
  blackout and lock contention. Uses existing protected research-refresh config.
- `deploy/illoura-review-import.path`, `deploy/illoura-review-import.service`:
  inbox change notification and bounded worker; deferred/failing work retries in
  five minutes without any new timer.
- `deploy/illoura-review-api.conf`: API sandbox exception limited to the inbox,
  shared read group and file-creation umask.
- `deploy/aws/illoura_aws.py`: scoped key creation, staged/hash-checked worker
  installation, API support-file deployment and rollback.
- `tests/test_review_inbox.py`, `tests/test_cloud_review_import.py`,
  `tests/test_aws_cli.py`, `api/test_security.py`: offline security and replay tests.

`GET /review` is an unauthenticated static shell with no data or external assets.
All `/v1/review/*` endpoints require a valid enabled key with the literal `review`
scope. Existing keys without scopes retain read access only. The API's existing
60-request/minute key limit still applies; review POSTs additionally allow 20/minute.
Request bodies require a bounded declared length, are also checked while streaming,
and may contain only the exact fields for their kind. Text rejects control characters;
evidence must be HTTPS without credentials. URLs are recorded, never fetched.

Submission fields shared by all kinds: `kind`, `target`, `expected` (SHA-256 of the
displayed current record), `evidence_url`, `notes`. Buyer adds `decision`
(`approved`/`rejected`) and `organisation_id` (null on rejection). Panel adds
`relevant` (boolean/null) and `illoura_status` (`unknown`, `member`, `applied`,
`not_applied`, `not_eligible`). Profile adds `value`. Unknown fields fail validation.
Server adds a UUID decision `id`, key-name `reviewer` and UTC `submitted_at`.

The 8 KiB body limit and 20 MiB inbox cap bound storage. One JSON line per
server-named `.jsonl` file remains retained after processing. Status files in the
separate ledger retain the complete decision, receipt time, processing time and
reason. API status responses disclose only the authenticated key name's decisions.
Profile notes are stored on the fact and in the ledger; panel evidence URL is
retained in the inbox/ledger, because watchlist marking has no evidence-URL column.
Panel marking does not change panel verification or clear `needs_manual_check`.

Idempotency is fail-closed: a durable `applying` ledger intent precedes database
writes. Completed IDs never replay. An interrupted intent becomes rejected with
an explicit reconciliation reason, including a crash between DB commit and ledger
completion. The operator must inspect existing database audit events before a new
submission; a file ledger and PostgreSQL cannot share an atomic commit. No schema
change or new database privilege is required. No evidence URL is fetched by the
importer and it does not acquire data, publish snapshots or send messages.

The live API is configured as one worker in `api/install-api.sh`; the per-process
write/rate locks assume this existing configuration. Do not increase workers
without replacing these limits with a cross-process implementation.
