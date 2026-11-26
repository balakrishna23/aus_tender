# Reference: intelligence, releases and lists

Turn collected evidence into classified opportunities, renewals, reviewable actions and briefs.

Files covered:

- `build_intelligence.py`
- `build_releases.py`
- `classify.py`
- `prime_briefs.py`
- `today_list.py`
- `mmr_list.py`
- `state_contracts.py`
- `listings_pipeline.py`
- `activity_log.py`
- `action_tracker.py`

The source databases remain separate from derived `intelligence.db` and `releases.db`. Derived rows retain source links, reasons and uncertainty. Human action edits live in `actions.db` so a rebuild can refresh evidence without replacing decisions. The YAML files used here are catalogued under [configuration.md](configuration.md); matching or scoring output is a proposal for review, not proof of supplier readiness or permission to contact anyone.

| Module | Inputs → outputs and main rule |
|---|---|
| `build_intelligence.py` | Source databases, reviewed identity/contact inputs and configuration → rebuilt intelligence tables in [databases.md](databases.md). Builds normalized opportunities; quarter-based hot plans; ICT/labour-hire renewals within a year; exemption history; panel memberships; contract/RFQ links and estimated rates; reviewed MMR entities/candidates; organisation identity and review views; actions/TODAY/prime briefs; optional quality projections. Writes `build_info` so readers can detect changed builds. |
| `build_releases.py` | Tender, BuyICT, ACT, NSW and newly published plan rows → `releases.db`. Keeps one source-keyed opportunity from the persistent feed-start date, tracks hashes/new/changed records and can export the new rows. Awards and standing offers stay out. `--since` selects the feed start; closing dates determine effective open/closed state. |
| `classify.py` | Cached YAML configuration → relevance, category family, role families, effective status and contact-safety labels. Category matches and include terms supply reasons; exclusion terms override. Weak wording alone can remain unclear. Actual closing time overrides a portal's open label. |
| `today_list.py` | Renewals, MMR evidence, exemption history, hot plans and live opportunities → owned, due-dated action proposals and a bounded TODAY list. Scores weighted components from `scoring.yaml`; thresholds select priorities. Missing facts use explicit assumptions/neutral components. Live procurement routes are formal-channel only. |
| `prime_briefs.py` | Actions, primes, candidate contracts, renewals, panels and BuyICT links → source-linked prime brief records and private HTML/text exports. Illustrative participation calculations are labelled hypothetical. No contact person, RAP promise, availability or rate is invented from missing evidence. Closed actions are excluded; exported brief selection is bounded. |
| `mmr_list.py` | Human-supplied NIAA MMR workbook → dated original `mmr_list`, supplier-ABN suggestions and review export in `organisations.db`. CLI flags `--load XLSX`, `--date DATE`, `--match`, `--export CSV` and `--review CSV` separate ingestion/matching from confirmation. Matching preserves review decisions. A name match does not establish an MMR contract's identity, value or end date. |
| `state_contracts.py` | Public WA CSV resources or supplied QLD workbook/CSV → `state_contracts.db/contracts` and `sources`. Maps columns, normalizes dates/values, classifies ICT relevance and retains licence/file/row provenance. Unchanged WA resources can be skipped; QLD imports match header words. |
| `listings_pipeline.py` | Source rows → normalized listings, deduplicated contact records and summary CSVs; optionally publishes Listings/Contacts/Summary and shares the sheet. `--crawl` invokes collection first; `--no-sheets` keeps the export local; `--sync-raw-tabs` adds raw source sync. Sector/state/inbox labels describe records, not contact authorization. |
| `activity_log.py` | Batch, item, Sheets/access and email events → best-effort append/update audit records in `activity.db`. Logging errors are caught so audit availability cannot break the caller. CLI report summarizes recorded activity. `ACTIVITY_RUN_ID` associates child events with the batch. |
| `action_tracker.py` | Existing Tracker edits + newly proposed actions → durable `actions`, per-field history and reconciled TODAY rows. Stable ID uses action type and organisation. Validates editable states, excludes closed/declined/snoozed work and brings due follow-ups back into view. |

| Configuration used | Role |
|---|---|
| `categories.yaml` | Category families, strong/weak/excluded terms and role classification. |
| `scoring.yaml` | Weights, INVESTIGATE/WATCH thresholds, TODAY bounds, renewal windows and stale-contact age. |
| `organisation_aliases.yaml` | Reviewed entity aliases/groups and optional ABN identity anchors. |
| `panels.yaml` | Panel identifiers and display names for membership interpretation. |
| `procurement_rules.yaml` | Jurisdiction/value gates and descriptive procurement treatments/participation targets. These are project assumptions, not a live legal rules service. |
| `seed_contacts.yaml`, `opt_outs.yaml` | Source-backed contact seeds and explicit suppression. Values are private and intentionally omitted. |

Downstream readers must avoid adding overlapping source and derived counts. Estimated day rates use contract value and calculated business days; they are not quoted candidate rates. Unreviewed identity matches and historical panels retain their qualification labels. Current data quality and availability of reviewed input files are unverified in this worktree.

## Spec analytical generation files

- `market_intelligence/views.py`: shared-snapshot builder pass, row shaping,
  conservative invitation/client matches and atomic generation publication.
- `market_intelligence/account_score.py`: strict lane-policy validation,
  percentile points, coverage, penalties and deterministic ranks.
- `market_intelligence/rules/account_score.json`: component definitions, data bases,
  per-lane weights and source/unknown notes.
- `market_intelligence/migrations/versions/025_spec_views.py`: retained generation
  schema, typed analytical views and pure SQL estimated rate groups.
- `tests/test_market_spec_views.py`: offline behaviour plus opt-in PostgreSQL
  rollback, retention and rate fixtures.

See [the analytical-view usage and limitations](../market_intelligence.md#spec-analytical-views-and-ranked-lanes-migration-025).
