# Federal market intelligence: first working milestone

For the 19 November APS, demand, retained RAP/state, panel, geography and newsletter continuation, see [market_completion.md](market_completion.md). Run `readiness --as-of DATE` for current coverage and missing business inputs; the full specification is not automatically accepted by passing engineering tests.

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

- National business registers for supplier identity checks (ABN bulk extract, ASIC
  companies, ACNC charities, ORIC corporations), described below.

Agency restructuring lineage, verified Illoura eligibility, panel intake/extension
monitoring, CRM/bench integration, ASIC business names, healthcare, RAP expansion
and JSA demand scoring are subsequent work. The downloaded datasets remain available
for those phases. No automated refresh has been added.

## National business registers and supplier checks

These registers live in their own tables (`market.register_*`, migration 009). They
never feed `market.organisations`, which treats every observation as a government
body, so they cannot change buyer matching.

| Source | What is kept | Scope |
|---|---|---|
| ABN bulk extract (ABR, weekly, CC BY 3.0 AU) | ABN status and date, entity type, ACN, GST status, DGR flag, state and postcode; organisation and trading names | ABNs in the current procurement snapshot (suppliers and buyers) |
| ASIC companies (monthly) | Current company name, former names, status, type/class, registration and deregistration dates | Same procurement ABN scope |
| ACNC charities (weekly) | Legal and other names, size, PBI/HPC, registration and establishment dates, beneficiary flags for Aboriginal or Torres Strait Islander people and health, states operated in, website | Complete register |
| ORIC corporations (monthly) | ICN, name, status, registration dates, size, industry, ACNC flag, latest income and employee bands | Complete register |

Privacy rules, applied before any database write and tested:

- Sole traders and other individuals keep only ABN status, entity type, state and
  postcode. Their names, including trading names that may contain a person's name,
  are never stored, compared or reported. A database constraint enforces this.
- Street addresses, towns, contact fields and people are never stored. Charity
  websites pass the same contact filter as government register websites.

ABN and ASIC files are imported only for the procurement ABN scope at import time,
and that scope list is stored (`market.register_scope`). A supplier added later shows
as `outside_import_scope` until the import is repeated; an ABN in scope but absent
from the public extract shows as `abn_not_found` (it may be suppressed or mistyped).
Text tables are read strictly as UTF-8, or else strictly as Windows-1252 (the ORIC
file uses Windows-1252). One malformed date becomes a record issue rather than a
failed import; missing required columns, duplicate identifiers and empty files fail.
Blank ABNs, and ASIC's `0`, mean "no ABN", not an invalid ABN.

`supplier-checks` compares every supplier ABN in the current analytical contracts
with the loaded registers and writes `supplier_checks.json` and `.html`:

- `invalid_contract_abn`, `abn_not_found`, `outside_import_scope`, `abn_cancelled`
  (with `cancelled_before_latest_award` when the ABN was cancelled before the latest
  award), `name_differs` or `consistent`.
- Name comparison writes legal forms one way ("Proprietary Limited" equals "Pty Ltd"),
  ignores a "The Trustee for" prefix, and accepts trading, business, former, ACNC and
  ORIC names. `same_core_name` means the names match once legal forms are removed.
- Flags: `indigenous_corporation_oric`, `registered_charity`,
  `company_registered_within_3_years`, `sole_trader`.

Every finding is a prompt for review, not evidence of wrongdoing: restructures,
trustees and trading names explain many differences. ORIC registration identifies an
Aboriginal and Torres Strait Islander corporation; it is not Supply Nation certification.

Name comparison also removes apostrophes ("MENS" equals "MEN'S"), joins initials
("M.G.S." equals "MGS"), compares each side of "ATF" and "T/A" separately, and treats a
label as `similar_name` when AusTender cut it short or it differs by a small typo
(string similarity of at least 0.9). ASIC business names (organisations only: a holder
the ABN register shows as an individual, or does not know, is skipped) add trading names.

## Categories, BuyICT buyers, Indigenous procurement levers, watchlist and profile

Editable rules live in `market_intelligence/rules/`, each with its source and retrieved date:

| File | What it holds |
|---|---|
| `categories.json` | Ordered rules that tag a contract `ict_labour`, `ict_services`, `labour_hire_general`, `recruitment`, `healthcare_staffing`, `nursing_agency` or `other` from the collector's category family, the UNSPSC code and keywords |
| `buyict_arrangements.json` | Standing offer IDs of the BuyICT marketplaces and People Panel arrangements, and which are in Illoura's categories |
| `ipp_rules.json` | Mandatory Set-Aside band ($80,000 to $200,000 GST inclusive, and all remote procurement), the $7.5 million MMR threshold and its 19 UNSPSC segments, 4 and 3 per cent targets, eligibility from 1 July 2026 (51 per cent, or ORIC registered), Exemption 16, and the rule that non-corporate Commonwealth entities must use coordinated arrangements; all from the NIAA IPP guidelines of May 2026 and the MSA factsheet |
| `ipp_portfolio_results.csv` | NIAA's published portfolio targets and results (2024-25 results, 2025-26 targets; data current at September 2025) |

The procurement export now also carries procurement method, exemption, the Indigenous
exemption flag, limited tender reason, supplier state and postcode, and the category tag
with the rule that matched. The description is read only to choose the tag and is never
exported. Generic descriptions ("Temporary Personnel", "Labour Hire") stay
`labour_hire_general`; the ICT share of labour hire is therefore a lower bound.

- `buyict-report`: agencies buying through BuyICT arrangements, 12 and 36 months, by
  arrangement and category, the sellers that keep winning (top 3 share), and non-corporate
  Commonwealth entities with no such purchase in linked notices. Invitation history stays
  unknown. Only notices AusTender links to a standing offer ID are counted.
- `levers-report`: agencies using Exemption 16; agencies buying our categories in the
  set-aside band outside BuyICT arrangements (remote delivery and CPR exemptions not
  checked); primes holding active contracts of $7.5 million or more in an MMR segment and
  our categories (subcontracting targets); NIAA portfolio results beside each portfolio's
  spend in our categories.
- `watchlist-seed` adds panels linked to contracts in our categories (36 months) plus the
  BuyICT arrangements, refreshing only spend and contract counts. `watchlist-export` writes
  a CSV for marking; `watchlist-mark --csv FILE --by NAME` applies only relevance, Illoura
  status and notes, logging every change. Intake dates, extension options and eligibility
  stay empty with `needs_manual_check` until a person verifies them.
- `profile-set --key K --value V --evidence-url https://... --by NAME` records Illoura's
  own facts (Supply Nation status, ownership, licences, clearances, panels joined), each
  change logged; `profile-show` lists them. Nothing is assumed.
- `category-summary` shows how contracts are tagged and the share of our families'
  value left in `other`.

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
python -m market_intelligence panel-worklist --as-of 2026-11-19 --output /private/reports/20261119-panels
python -m market_intelligence review-queue
python -m market_intelligence review-queue --output /private/reports/20261119-identities
python -m market_intelligence import-register --source oric --manifest /private/raw/run/manifest.json
python -m market_intelligence import-register --source acnc --manifest /private/raw/run/manifest.json
python -m market_intelligence import-register --source asic_companies --manifest /private/raw/run/manifest.json
python -m market_intelligence import-register --source abn --manifest /private/raw/run/manifest.json
python -m market_intelligence import-register --source asic_business_names --manifest /private/raw/run/manifest.json
python -m market_intelligence supplier-checks --as-of 2026-11-19 --output /private/reports/20261119-suppliers
python -m market_intelligence buyict-report --as-of 2026-11-19 --output /private/reports/20261119-buyict
python -m market_intelligence levers-report --as-of 2026-11-19 --output /private/reports/20261119-levers
python -m market_intelligence watchlist-seed --as-of 2026-11-19
python -m market_intelligence watchlist-export --output /private/reports/20261119-watchlist.csv
python -m market_intelligence watchlist-mark --csv reviewed.csv --by REVIEWER
python -m market_intelligence profile-set --key supply_nation_status --value Certified --evidence-url https://example --by REVIEWER
python -m market_intelligence category-summary
```

Import procurement first: the ABN and ASIC imports read their scope from the current
procurement snapshot. The ABN import streams all twenty XML files inside the two ZIPs
without extracting them; it took 16 minutes for 20.7 million records on the 8 GB server.
Business names need the ABN register loaded first (holder type decides what is kept).

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

The panel worklist groups observed standing offer IDs across source buyer labels
and retains associated buyer ranks, identity review states and source provenance.
Repeated route rows do not inflate counts. Conflicting expiry claims remain
unresolved. A recorded expiry is not an application deadline or evidence of an
open or closed intake; membership, eligibility and invitations remain unknown.
Counts across different panels may overlap. Check the current official notice,
category relevance and Illoura's documentary evidence before assessing access.
The JSON includes the source buyer-report code hash and a separate hash of the
worklist implementation. This command creates internal artifacts only.

The optional review-queue output is a read-only HTML/JSON packet with the source
buyer name/ABN, candidate register observations, snapshot IDs and source hashes.
Register sources and the proposal generation are read in one consistent database
transaction. Changed identity evidence, stale proposal context and source conflicts
appear before straightforward proposals. Exporting a packet records no decisions.
Saved packets can become stale; recheck the current queue before recording a review.

## RAP directory, APS 2026, aged care and automatic refresh (23 November 2026)

- **RAP directory** (`acquire_rap.py`, `rap_review.py`; commands `acquire-rap`,
  `review-rap`, `verify-rap`, `import-research --rap-collection`). It downloads the
  public Reconciliation Australia directory after a robots check and retains the
  responses privately. A directory-only service account (its UUID comes from
  `ILLOURA_RAP_DIRECTORY_REVIEWER_IDS`) clears only rows that pass every check:
  organisation-name heuristics, register cross-matches, URL rules, RAP type and
  period, and duplicates. Everything else is quarantined with reasons, and an
  independent verifier replays the evidence and the payload digest before import.
  The project owner approved this automated reviewer on 20 Nov 2026.
- **APS 2026** (`aps.py` parser revision 2) reads the 30 June 2026 job-family model.
  ICT family comparisons with earlier years are flagged as not like-for-like.
- **Aged care** (`acquire_health.py`, `aged_care.py`, migration 022; commands
  `acquire-health`, `import-aged-care`, `aged-care-report`) imports the quarterly
  Star Ratings extract as quality evidence only. An older quarter is kept as
  history and never replaces a newer current quarter. Same-period corrections
  replace it.
- **Freshness and automatic refresh** (`freshness.py`, `research_refresh.py`,
  `acquire_wa.py`, migration 023; commands `freshness-report`, `research-refresh`,
  `inbox-status`). Each source has a cadence and is either automatic or manual.
  data.gov.au sources (ABN, ASIC, ACNC, AGOR, government directory, ORIC) are
  manual because its robots.txt disallows all automated access. A person drops the
  release into the private inbox, and the refresher validates and imports it with
  no network requests. `deploy/illoura_research_refresh.py` and the
  `illoura-research-refresh` timer (10:15 Sydney) take the shared morning lock and
  skip the 05:30–09:30 Sydney brief window. They run for at most 45 minutes and
  are installed disabled. See `deploy/research-refresh-handoff.md` for install
  and validation steps.

## Tests and operations

```text
uv run --no-project --with-requirements requirements-market.txt python -m unittest discover -s tests -p "test_market_*.py"
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

## Spec analytical views and ranked lanes (migration 025)

Run `python -m market_intelligence migrate`, then
`python -m market_intelligence refresh-views --as-of 2026-11-27`.
Migration head is `025_spec_views`, following `024_national_registers`. The local
024 placeholder must be replaced by the coordinating branch before deployment.
No network access, contact enrichment, outreach, or email is part of this command.

Metabase can browse `market.v_account_score` and filter `lane` to obtain one
ranked list for federal, state, private, or healthcare. `rank_in_lane` gives lane
order; `overall_rank` orders all lanes by research points, with different evidence
bases rather than a calibrated cross-lane win probability. `components`,
`penalties`, `score_coverage_pct`, `blockers`, `why_now`, `target_roles`, evidence,
and identifiers explain every row. Use `new_target=true` for new-account research:
active/researching/past-client relationships and won outcomes are marked already
engaged, and do-not-pursue records are never new targets. Private CRM activity and
reviewer names are not copied into generated rows.

New generation-backed views: `v_buyict_buyers`, `v_buyict_gaps`, `v_ipp_gap`,
`v_ipp_levers`, `v_prime_targets`, `v_partner_candidates`, `v_indigenous_partners`,
`v_rap_targets`, `v_panel_access`, `v_forward_pipeline`, `v_demand_vs_bench`,
`v_lookalikes`, `v_account_score`, and `v_whats_changed`. They expose typed identity,
measure and evidence columns plus full shaped `details` JSON. Existing 018/022/023
views continue to supply spend, supplier, healthcare, quality and freshness facts.
The read-only reader receives SELECT access. `v_agency_org_map` belongs to the
coordinating branch. **`v_grant_triggers` is skipped: no grant data exists.**

`views.refresh_views` calls the existing builders once through one repeatable-read
connection. A session lock serialises publishers before opening that snapshot.
Rows, generation metadata and the current pointer commit together: failures roll
back, retaining the previous current generation. `view_generations` keeps all run
metadata; only derived `view_rows` older than the newest 30 generations are
removed. Every view has a 10,000-row cap (account score reserves up to 2,500 rows per lane), recorded alongside its total in generation
metrics; account rankings are computed before truncation. Source limitations,
weekly baseline gaps, config and config SHA-256 are retained. A source manifest
code-bundle hash is recorded when available. The daily research refresher executes
a final isolated `views` step, records started/finished attempts, and returns its
result separately from source outcomes. It is deliberately absent from the
morning helper. A failed view build cannot roll back successful source promotions.

`rules/account_score.json` is a strictly validated analyst policy. Positive weights
sum to one in each lane. Inputs use within-lane midrank percentiles, known zero
receives zero points, and unknown inputs remain null with a reason and retain
their weight: they are never rescaled. All-unknown accounts have null scores.
Coverage is the sum of known positive weights; penalty coverage is separately
visible. Blocker penalty is up to 20 points, insourcing up to 10. Insourcing has no
source and stays null; eligibility uses known panel requirements and Illoura profile
values or an approved RAP certification requirement. Missing requirements/profile
values produce an unknown penalty, never an assumed pass or failure.

| Lane | Positive components (percent weights) |
| --- | --- |
| Federal | spend 20; frequency 10; expiry in 6-12 calendar months 10; BuyICT gap 8; IPP gap 7; levers 7; forward plans 7; open notices 5; RAP 4; lookalike 5; panel access 5; grants 3; budget 3; demand vs bench 6 |
| Private | RAP type 20; plan ends within 12 months 15; approved procurement target 10; approved employment target 10; supplier value 20; MMR-prime signal 15; lookalike 10 |
| State | historical WA value 50; historical WA observation frequency 30; lookalike 20 |
| Healthcare | inverse staffing stars 40; inverse overall stars 30; published size 20; RAP 10 |

Federal spend uses the existing buyer report's category families and 365-day
original award values; awards are not payments. BuyICT measures reuse the builder's
relevant arrangement coverage over 36 months. The RFQ-invite list matches Illoura's
invitation input on exact buyer keys or unique normalised full buyer labels only.
Each row explains the basis: **no recorded invitation is not proof of never being
invited**. A matched invitation suppresses that gap. IPP gaps require a reviewed,
current agency identity to attach historical portfolio results. Panel access is
linked panel value where recorded membership is not `member`; any unknown
membership keeps the component null. Plans with unknown dates do not score as a
next-year plan. Empty forward-source coverage remains unknown.

Private candidates comprise current RAP holders plus registry-named suppliers in
our categories with at least AUD 1 million of original awards over 36 months
(the configurable large-supplier proxy, not turnover), plus all safe-name MMR primes. Contract supplier labels are never
used as a fallback, since they can identify individuals. Omitted primes without a
safe organisation name are counted in metrics. RAP candidate ABNs remain proposals
and are not silently merged with supplier accounts. State coverage is retained WA
FY2021-22 through FY2023-24 observations only; counts may include repeated source
records and are not current addressable spending. Healthcare includes retained
low staffing/overall aged-care services and ACNC health-purpose/ORIC health
corporations. Ratings are research signals, not buying intent. No account-level
bench attribution exists, so federal demand-vs-bench stays unknown; the regional
aggregate comparison remains browsable. No grants, budget-measure or insourcing
feed exists, so those components remain unknown too.

Lookalikes use only recorded past-client relationships and won outcomes whose
stable keys resolve into the current account universe. Type, published size,
reviewed portfolio and tagged category-spend vector each have 25% similarity
weight. Unknown dimensions retain their weight; no past-client evidence produces
a null similarity. Exact unique current-RAP name matches are research proposals,
not confirmed legal-entity joins. Group RAPs, service sites and register records
can remain separate where identity is unresolved.

`v_rate_benchmarks` is a pure SQL view over current analytical labour/ICT-labour
contracts. The native privacy-safe export contains no headcount or description;
the single-resource proxy uses labour tags and plausible rate/duration bounds. It
rejects multiple-resource/team wording if future payloads supply it, requires recorded
dates from 1990 through 2099,
positive value, 21-1,827 inclusive calendar days and an implied calendar-day rate
between 100 and 2,500. This is a **single-resource heuristic**, not verified
headcount. The bounds truncate the sample and multiple people may still be included.
Estimates use current full contract value divided by inclusive calendar
duration, with annual estimates multiplied by 365.25. They are not working-day
rates, and amendments, leave, GST and scope can distort them. Groups expose buyer,
role category, supplier state (null when absent; delivery/agency state unavailable), sample size, average/median calendar-day
estimate and annual estimate. Never use them as verified bid prices.

The Monday digest compares retained research/contract/panel evidence, forward
plans against a pre-week view generation where available, and recorded contract
ends within the coming week. Missing comparison history is a baseline gap, not a
batch of new opportunities. Grants cannot appear without a grant input source.
All new views are bounded read-API export tables (10,000 rows each); `account_score`
is indexed by lane, account key and lane rank. Generation row counts are available
in PostgreSQL for cap review; the API export is a bounded convenience copy.

Verification: `tests/test_market_spec_views.py` covers config/math/nulls/coverage,
penalties, engagement, shaping/privacy, invitation matching, lookalikes, shared
snapshot use, builder reuse, export parity, and opt-in PostgreSQL publication,
rollback/retention and SQL rate fixtures. PostgreSQL tests require
`ILLOURA_MARKET_TEST_DATABASE_URL` targeting the isolated `illoura_market_test`
database. They create derived generation fixtures and never alter source history.
