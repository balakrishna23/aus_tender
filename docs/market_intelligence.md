# Federal market intelligence: first working milestone

This package adds an isolated PostgreSQL research layer around the existing
procurement collectors. It does not replace SQLite collection, the API publication
generation, the daily brief, or any schedule. Reports are private drafts.

## What is implemented

- Manifest-verified AGOR and Government Directory imports, including strict
  Windows-1252/UTF-8 handling, source dates, licences and checksums.
- Immutable source observations and parser revisions. The current snapshot only
  advances after a successful transaction. Older imports never replace newer data.
- Source-provided organisation IDs, rather than names or ABNs, identify government
  body records. Government bodies can share an ABN without being the same body.
- Organisation/portfolio/unit metadata only. Directory people and role records,
  direct contact fields and free-text descriptions are excluded before DB writes.
  Website fields are validated as web URLs; embedded email/contact strings are
  excluded too. Migration 008 redacts affected older derived website values while
  preserving restricted original source files and an anonymous remediation count.
- Read-only exports from existing procurement databases. A separate, contact-free
  snapshot contains contract facts, contract-to-panel links and panel notices.
- Versioned buyer identity proposals, independent human approve/reject decisions,
  immutable review events and recheck flags when official identity evidence changes.
- A draft buyer report for federal ICT services and labour hire/recruitment, with
  contract evidence, supplier evidence, known panel routes and score components.
- Quality reporting, including source conflicts, missing links and privacy checks.

National ABR/ASIC/ACNC ingestion, agency restructuring lineage, verified Illoura
eligibility, panel intake/extension monitoring, CRM/bench integration, healthcare,
RAP expansion and JSA demand scoring are subsequent work. The downloaded datasets
remain available for those phases. No automated refresh has been added.

## Identity and evidence rules

`gov:<publisher_id>` identifies a published government body, portfolio or unit. It
does not claim that every publisher record is an independent legal entity. The
`organisations` view chooses the most recently observed current source record for
each publisher ID; all current official observations remain separately available.
Disagreement between source names or nonempty ABNs is flagged, not silently resolved.

Buyer matching uses valid ABNs and exact normalized official names. A shared ABN
can be disambiguated by one exact official name within that ABN's candidate set.
This links a buyer label to a body; it does not merge bodies sharing an ABN. Fuzzy
matches are review candidates only. Missing ABNs and contradictory evidence remain
explicit. Exact machine matches are proposals until a person approves them.

AGOR's effective date is the date in the publisher's snapshot label. Directory is
a rolling export: its date basis is retrieval, not a claim that every record was
updated that day. Matching records preserve both source contexts. `review-queue`
shows the official candidate evidence. Human decisions are never overwritten by
an automated rebuild. A change to any current official identity claim requires
rechecking an approval; unchanged observations in a new snapshot do not.

Legacy amendment-only rows in the existing contract rollup are retained with an
issue flag and excluded from research totals. The new layer does not guess a
base award value or alter the collector's records.

## What the buyer report means

The default configuration is `market_intelligence/scoring.json`. Its weights are
draft business choices, not a calibrated probability of winning work. The report
uses relative percentiles for original awarded value, award frequency and recorded
end-date counts. It shows the inputs and their coverage. Missing monetary values
remain unknown; missing inputs are not rescaled to a full score. A verified absence
of recent awards within the stored snapshot contributes zero, rather than an
invented positive signal.

- **Original awarded value:** original notice values first published in the
  trailing 365 days. This is not payments, annual expenditure or addressable revenue.
- **Full value of contracts ending:** whole amended contract values for recorded
  end dates in the next 365 days. This is not remaining value or an announced retender.
- The two windows can overlap. The overlap count is explicit; monetary totals must
  not be added together as a forecast pipeline.
- Supplier rows are evidence from these windows, not a claim of an exclusive incumbent.
- Panel links cover the collected notice pages. No link means unknown, not no panel.
- Notice periods do not establish extension options, intake dates or eligibility.
- Invitation history, Illoura access, certification, candidate availability and
  delivery readiness remain separate and unverified. No neutral assumptions inflate
  the priority score. Source buyer labels remain separate until identity review.

The source collector checkpoint, rather than export time, supplies the freshness
warning. The JSON contains source metadata, source dates, snapshot IDs, parser
revisions, config checksum, evidence sample counts and known limitations. Historical
`--as-of` dates do not reconstruct the state of older contracts; a warning explains
when newer snapshots are being used.

## Local setup

Use Python 3.11+ and PostgreSQL 16. Install the isolated dependencies:

```powershell
uv run --no-project --with-requirements requirements-market.txt python -m market_intelligence --help
```

`compose.market.yaml` is the optional local development setup. Set separate private
`MARKET_ADMIN_PASSWORD` and `MARKET_DB_PASSWORD` variables before running
`docker compose -f compose.market.yaml up -d`. It binds only `127.0.0.1:55432` and
creates a non-superuser application role through `compose-init.sh`. Initialization
only runs on a new Docker volume; changing environment values does not rotate an
existing database password. Docker setup is not the cloud deployment path used in
this milestone and requires a local Docker smoke check before relying on it.

Set `ILLOURA_MARKET_DATABASE_URL` privately using the SQLAlchemy
`postgresql+psycopg://` scheme. Never put credentials in repository files, command
arguments, reports or Git. The cloud setup stores this value in a mode-0600 file
under its mode-0700 private data directory. It uses native PostgreSQL 16, a separate
database, an application role without superuser/role-creation/database-creation
permissions, localhost binding and conservative memory/connection settings.

## Commands

Run from the repository/build root with the private database environment loaded:

```text
python -m market_intelligence migrate
python -m market_intelligence import --source agor --manifest /private/raw/run/manifest.json
python -m market_intelligence import --source government_directory --manifest /private/raw/run/manifest.json
python market_intelligence/extract_buyers.py --source /collector/contract_api.db --destination /private/buyers.db
python -m market_intelligence bridge-buyers --contracts-db /private/buyers.db
python market_intelligence/extract_procurement.py --directory /collector/databases --destination /private/procurement.db
python -m market_intelligence import-procurement --database /private/procurement.db
python -m market_intelligence quality --as-of 2026-11-19
python -m market_intelligence buyer-report --as-of 2026-11-19 --output /private/reports/20261119
python -m market_intelligence review-queue
```

The export commands need read permission on collector files. The operator can run
only those exporters with elevated local access, then transfer ownership of their
private outputs to the application operator. The PostgreSQL application never
needs a superuser connection or write permission on collector databases. Independent
SQLite sources may have different collection dates, which remain visible.
Export destinations are immutable: use a new dated directory for each acquisition.
An existing export is never replaced, including when an export is run twice.

The application does not load `.env` implicitly. On the cloud server, an authorised
operator loads `/home/ubuntu/illoura-market-data/database.env` without printing it.
The isolated virtual environment and staged source are under that private directory.

Explicit review example (placeholders only):

```text
python -m market_intelligence review --buyer-key BUYER_HASH --decision approved --organisation-id gov:O-000001 --reviewer REVIEWER_ID --evidence-url https://official.example/evidence --notes "Identity checked against the published record"
python -m market_intelligence review --buyer-key BUYER_HASH --decision rejected --reviewer REVIEWER_ID --evidence-url https://official.example/evidence --notes "Identity unsupported"
```

Review decisions require an HTTPS evidence URL and reviewer identifier. The CLI
does not send outreach or trigger a newsletter. This pipeline is for internal
research; subsequent outreach must comply with the Spam Act and Privacy Act.

## Tests and operations

```text
uv run --no-project --with-requirements requirements-market.txt python -m unittest discover -s tests -p test_market_intelligence.py
```

Parser, identity, scoring and export tests run without a database. PostgreSQL
integration tests require a private `ILLOURA_MARKET_TEST_DATABASE_URL` pointing to
the dedicated `illoura_market_test` database; they refuse any other database name.
They truncate only that disposable database's test tables. They cover rollback,
idempotency, history, database constraints, source conflicts, privacy checks,
review persistence/rechecking and bulk-copy failure. Successful test runs with
database tests skipped do not prove the integration works.

Migrations use Alembic with a migration lock. Later downgrade paths deliberately
refuse to discard history. Recovery uses a forward fix or a validated dump, not a
blind schema rollback. Production-like imports are staged separately from served
API generations; nothing here switches the existing API's data pointer.

A cloud dump/restore drill was performed into a new isolated restore database,
with row-count and source-hash comparison. The current one-off scripts and evidence
are in the private workspace and cloud data directory. Keep the raw downloads,
manifests, code revision, scoring configuration and PostgreSQL dump together for
reproducibility. Local dumps do not protect against losing the entire disk; the
existing whole-instance snapshot policy is separate. Off-instance application dumps
and automatic refresh/backup scheduling still require implementation and validation.
