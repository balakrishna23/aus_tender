# Market intelligence continuation — 19 November 2026

This extends the isolated federal research build. It does not claim completion of the full 48-source specification. `python -m market_intelligence readiness --as-of DATE` reports measured coverage and missing inputs. Acquisition dates and licences remain attached to the data. `sources.yaml` records available and pending sources, with intended cadences rather than enabled schedules.

## Implemented data

| Data | Scope |
|---|---|
| APS employment | June 2025: 104 rows including two ceased agencies; 102 current agencies, 198,529 staff, 95 proposed register associations |
| JSA vacancies | 41,886 observations: latest 13 published months of the four-digit state/territory series |
| JSA shortages/projections | 311 national ratings; 1,422 baseline/projection observations |
| Tender Feed | 19,267 contact-free retained plan/notice observations; repeated source references preserved separately |
| RAP | 1,406 directory observations collected 27 October; 26 existing reviewed page-cited commitments, attached only to matching document hashes |
| State contracts | 16,751 retained WA observations from 2021–22 to 2023–24; 2,986 relevant tagged rows; historical coverage only |
| Supplier research | 3,686 suppliers with relevant original awards over 36 months; 339 young/ORIC research candidates |
| Healthcare research | 8,306 ACNC health-purpose/ORIC health-industry source records, potentially overlapping; buying and staffing need unknown |
| ABS geography | SA4, LGA and POA boundary importer, native GDA2020 coordinates; no remoteness or postcode crosswalk inferred |

The APS tables reconcile to their published totals. Missing cells remain distinct from zero. Rejected/stale buyer proposals are excluded from APS spend associations. Partial identity approval never becomes full approval.

New HTML reports escape source text; new CSV reports protect text beginning with spreadsheet formula characters. SQL views `v_agency_spend`, `v_supplier_profile`, `v_healthcare_accounts`, `v_demand_signals` and `v_panel_calendar` expose the evidence actually loaded.

## Run and refresh

Use the private PostgreSQL environment and pinned `requirements-market.txt`. The application does not implicitly load `.env`. Never print or commit credentials.

```text
python -m market_intelligence migrate
python -m market_intelligence import-aps --manifest REVIEWED_MANIFEST
python -m market_intelligence import-demand --source jsa_ivi --manifest MANIFEST
python -m market_intelligence import-demand --source jsa_shortages --manifest MANIFEST
python -m market_intelligence import-demand --source jsa_projections --manifest MANIFEST
python -m market_intelligence.extract_forward --source INTELLIGENCE_DB --destination NEW_EXPORT_DB
python -m market_intelligence import-forward --database NEW_EXPORT_DB
python -m market_intelligence.extract_research --source rap_directory --input RETAINED_LIBRARY --destination NEW_JSON
python -m market_intelligence.extract_research --source rap_commitments --input RAP_EVIDENCE_DB --destination NEW_JSON
python -m market_intelligence.extract_research --source state_contracts --input STATE_CONTRACTS_DB --destination NEW_JSON
python -m market_intelligence import-research --json NEW_JSON
python -m market_intelligence import-geography --layer SA4 --manifest MANIFEST
```

Repeat the geography command for LGA and POA. Reports accept `--as-of DATE --output DIRECTORY`: `aps-report`, `demand-report`, `forward-report`, `rap-report`, `state-report`, `targets-report`, `geography-report`, `panel-access`, `bench-report`, `market-digest`. `changes-report` also takes `--since DATE`. A first imported snapshot is labelled as a baseline, never as newly published opportunities. Reports carry source-date limitations; historical as-of dates do not reconstruct all historical source states.

Raw exports and source bundles use new immutable destinations. Repeated imports of unchanged content retain the current snapshot. Keep raw downloads, acquisition manifests, source bundle checksums, pinned dependencies and PostgreSQL backups together. Test restores into a new isolated database; downgrade migrations do not discard history.

## Weekly work for Swetha

1. Review buyer packets, with changed evidence/conflicts first. Exporting a packet records no approvals.
2. Mark relevant panels. Verify current routes, intake and expiry facts from official documents. `panel-verify --json FILE --by REVIEWER` requires a source URL, check time and document SHA-256. It records an audit history. Notice expiry alone is not an intake deadline.
3. Confirm Illoura profile facts and documentary evidence. Missing insurance, licensing, certification and clearances remain unknown. Panel reminders use 12, 6 and 3 calendar months and send no messages themselves.
4. Run `input-templates --output NEW_DIRECTORY`. Supply the real aggregate bench, exact occupation mappings, CRM status, invitation records, bid outcomes and verified lineage. `import-input --kind KIND --csv FILE --source-url HTTPS_EVIDENCE --as-of DATE --by REVIEWER` rejects extra/contact columns, duplicate identities, invalid counts and future observations. Blank templates cannot be imported as evidence.
5. Check RAP edition and legal-entity scope. Commitments do not prove delivery, missed targets, available budget or acceptance of Illoura.
6. Review changes and freshness. A missing invitation in public award data cannot prove Illoura was never invited. Bench reports remain empty until real bench counts and mappings exist.

## Newsletter

`market-digest` generates an internal package with buyer history, panel dates to verify, same-document page-cited RAP evidence, published workforce indicators and a review checklist. The daily composer can consume private `market_research.json`. Its reader validates schema, source URLs, generation time, size and a maximum seven-day research window. Section headings show the research date. Missing, stale or malformed research is recorded as unavailable and cannot cancel the live procurement brief. Live RFQs keep their independent current-listing checks.

Generating a package never sends mail. The existing morning brief now runs a bounded research refresh after collection and before RFQ composition. It exports current collector facts without contacts, refreshes buyer proposals without recording approvals, rebuilds reports, validates the new package and publishes it atomically. Failed research retains the previous dated package and cannot prevent live RFQ composition. The refresh has a 210-second maximum and reserves the RFQ composition/send budget. No new timer, outreach or additional send is created.

`deploy/illoura_market_refresh.py` is the root-owned coordinator. It runs source exports as the scraper and application/database work as the unprivileged research owner. The database URL remains in the private environment file and never appears in argv. Root-owned `/etc/illoura-market/refresh.json` points at a validated build and protected export tools; `deploy/market-refresh.example.json` documents the shape. Each run retains an immutable export/report directory and records status. A lock prevents overlapping refreshes, and previous published packages are retained by checksum.

This automatic stage refreshes current collector snapshots and derived research. AGOR, ABN, JSA, RAP and other external publishers retain their recorded acquisition/release dates until their separate acquisition is refreshed. A new digest generation timestamp does not make an old publisher release current.

## Remaining acceptance work

The full-active-ABN target, 95% reviewed buyer-value coverage, panel marking, broad RAP matching/extraction accuracy and business review of top targets remain unmet. Register scope is still procurement ABNs. Broad category fallback accounts for 79.3% of original award value in the two broad ICT/labour-hire families and needs tuning against real examples.

NDIS bulk acquisition returned an HTTP error from the VM and a local probe timed out; no NDIS coverage is claimed. Other states, current named healthcare providers/ratings, grants, budget/insourcing and annual-report feeds still need verified acquisition and source-specific parsers. Full RAP LLM extraction/cost accounting, Metabase, outbound CRM sync and automatic acquisition of the remaining external publishers remain unimplemented. Public provider purpose/registration is not evidence of workforce need. Commercial reuse must be checked per source; retained RAP and ORIC evidence stays internal.

The pipeline does not send buyer/partner outreach. Any later outreach is a separate authorized workflow subject to the Spam Act and Privacy Act.
