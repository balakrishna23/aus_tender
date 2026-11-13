# Reference: tests

Describe each test file and separate fixture checks from Linux deployment and live smoke verification.

Files covered:

- `api/test_security.py`
- `local_assistant/http_smoke.py`
- `local_assistant/hybrid_ui_smoke.py`
- `local_assistant/report_smoke.py`
- `local_assistant/sheets_sync_smoke.py`
- `local_assistant/sheets_ui_smoke.py`
- `local_assistant/test_assistant.py`
- `local_assistant/test_buyict_sellers.py`
- `local_assistant/test_company_coverage.py`
- `local_assistant/test_contact_discovery.py`
- `local_assistant/test_hybrid.py`
- `local_assistant/test_reasoning.py`
- `local_assistant/test_refinements.py`
- `local_assistant/test_route_review.py`
- `local_assistant/test_sheet_data.py`
- `local_assistant/test_verification.py`
- `local_assistant/test_website_leads.py`
- `local_assistant/ui_smoke.py`
- `routine/scripts/test_sender.py`
- `tests/test_action_tracker.py`
- `tests/test_activity_log.py`
- `tests/test_audit_fixes.py`
- `tests/test_austender_api.py`
- `tests/test_aws_cli.py`
- `tests/test_backup_databases.py`
- `tests/test_bdm_local_runner.py`
- `tests/test_bdm_monitor.py`
- `tests/test_bdm_workflow.py`
- `tests/test_build_intelligence.py`
- `tests/test_classify.py`
- `tests/test_cloud_collect.py`
- `tests/test_cloud_daily_brief.py`
- `tests/test_cloud_health.py`
- `tests/test_cloud_ops.py`
- `tests/test_cloud_retention.py`
- `tests/test_collectors_pending.py`
- `tests/test_collectors_silent_output.py`
- `tests/test_collectors_unpublished.py`
- `tests/test_compliant_routes.py`
- `tests/test_crawl_integration.py`
- `tests/test_data_quality.py`
- `tests/test_data_quality_alerts.py`
- `tests/test_export_views.py`
- `tests/test_full_details.py`
- `tests/test_incremental.py`
- `tests/test_listings_pipeline.py`
- `tests/test_mmr_list.py`
- `tests/test_outreach.py`
- `tests/test_outreach_agent.py`
- `tests/test_planned_procurement.py`
- `tests/test_prime_briefs.py`
- `tests/test_public_research.py`
- `tests/test_publish_engine_views.py`
- `tests/test_publish_sheets.py`
- `tests/test_releases.py`
- `tests/test_routine_coverage.py`
- `tests/test_routine_mail_config.py`
- `tests/test_routine_mail_env.py`
- `tests/test_routine_run_once.py`
- `tests/test_routine_sender.py`
- `tests/test_snapshot.py`
- `tests/test_state_contracts.py`
- `tests/test_today_list.py`
- `tests/test_workflow.py`
- `tests/fixtures/ocds_CN3695219.json`
- `.github/workflows/tests.yml`

Tests listed here were read, not run. Most unit suites use temporary SQLite, local fixtures and mocked external clients. Browser smoke/evaluation scripts are separate operational checks and can require data, running services or network access; do not include them in an offline unit run. Local AI remains disabled. Windows commands below assume Python 3.11 and the relevant dependencies already installed.

```powershell
$env:GSHEETS_AUTO_SYNC = 'False'
python -m unittest discover -s tests -p "test_*.py" -v
python -m unittest discover -s local_assistant -p "test_*.py" -v
python -m unittest discover -s api -p "test_security.py" -v
python -m unittest discover -s routine/scripts -p "test_sender.py" -v
```

The API suite additionally needs `api/requirements.txt`; optional Gmail authorization needs `requirements-bdm.txt`. `.github/workflows/tests.yml` runs Python 3.11 on Ubuntu and Windows, installs `requirements-tested.txt`, and runs the first two suites. It does not currently run the separate API or standalone sender discovery commands. The OCDS JSON fixture supplies fixed contract/version evidence for tests; it is not a production database.

| File | What it protects | Platform / execution |
|---|---|---|
| `api/test_security.py` | API authentication/allowlists, read-only SQL/resource bounds, freshness/pinned generations, MCP and bounded client/key throttling. | Unit/fixture suite; dependencies required. |
| `local_assistant/http_smoke.py` | Running assistant HTTP responses without exposing the browser session token; requires a running local service. | Manual smoke check; prerequisites apply. |
| `local_assistant/hybrid_ui_smoke.py` | Browser semantic controls, evidence and exact constraints; requires local UI/runtime data. | Manual smoke check; prerequisites apply. |
| `local_assistant/report_smoke.py` | Standalone handover report layout via browser, without upload; requires generated report/browser. | Manual smoke check; prerequisites apply. |
| `local_assistant/sheets_sync_smoke.py` | Authorized explicit UI import endpoint; makes read-only Sheets calls and writes the local mirror. | Manual smoke check; prerequisites apply. |
| `local_assistant/sheets_ui_smoke.py` | Browser imported-Sheets controls and provenance; requires local UI and imported data. | Manual smoke check; prerequisites apply. |
| `local_assistant/test_assistant.py` | Read-only parameterized retrieval, missing-database behavior, evidence identity, current deadlines and no invented model facts. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_buyict_sellers.py` | Company-only schema, ABN/website cleaning and complete public catalogue pagination. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_company_coverage.py` | Panel supplier inclusion and accurately counted reviewed approachable routes. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_contact_discovery.py` | Business contact extraction, identity/brand/private-network/robots restrictions, page priorities and resumable outcome recording. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_hybrid.py` | Lexical/semantic fusion, exact eligible text/model cache identity, missing-index failure and invalid-vector rejection. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_reasoning.py` | Plan constraints, exact role/entity/value/date/ABN retrieval, full-set counts and constrained semantic fallback. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_refinements.py` | Negation/synonyms/windows/ABN parsing, unknown facts and historical research scope labels. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_route_review.py` | Functional business-route ranking, exclusion of personal/foreign routes and explicit approved-only imports. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_sheet_data.py` | Versioned imported tabs, literal scope/row provenance, unchanged/failed reads and unsupported financial-filter disclosure. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_verification.py` | Official contract identity/amendments/Canberra dates, mismatches/outages and URL/redirect rejection. | Unit/fixture suite; dependencies required. |
| `local_assistant/test_website_leads.py` | Supplier website gaps, evidence-backed ABN linkage, candidate validation and complete research logs. | Unit/fixture suite; dependencies required. |
| `local_assistant/ui_smoke.py` | Optional Playwright UI behavior against a running assistant and installed browser. | Manual smoke check; prerequisites apply. |
| `routine/scripts/test_sender.py` | Standalone routine sender duplicate/hash/recipient/source-gap gates using mocked Google transport. | Unit/fixture suite; dependencies required. |
| `tests/test_action_tracker.py` | Stable action IDs, persistent human edits, valid states and due/closed TODAY behavior. | Unit/fixture suite; dependencies required. |
| `tests/test_activity_log.py` | Run/item/scrape/share/access recording and nonfatal audit failures. | Unit/fixture suite; dependencies required. |
| `tests/test_audit_fixes.py` | Currency/location migrations, safe literal staged Sheets writes, filters/grid sizes and collector failure propagation. | Unit/fixture suite; dependencies required. |
| `tests/test_austender_api.py` | Immutable versions, exact rollups, extension/exemption/date fields, cursor paging, resumable history and retries. | Unit/fixture suite; dependencies required. |
| `tests/test_aws_cli.py` | Fixed remote actions, pinned/staged/hash-checked deploys, verify-only behavior, rollback, timer invariants and private configuration handling. | Unit/fixture suite; dependencies required. |
| `tests/test_backup_databases.py` | Weekly API backup deduplication, daily reviewed-input copies and two-weekly-copy retention. | Unit/fixture suite; dependencies required. |
| `tests/test_bdm_local_runner.py` | Failure status, refresh without email, dynamic staleness and idempotent local journal. | Unit/fixture suite; dependencies required. |
| `tests/test_bdm_monitor.py` | Silent baseline/revisions, closure relevance, preserved edits, atomic imports, internal-recipient restrictions and uncertain-send gates. | Unit/fixture suite; dependencies required. |
| `tests/test_bdm_workflow.py` | Qualification versions/evidence, stale reconciliation, reminder deduplication, same-origin edits, locks and original legacy-tracker updates. | Unit/fixture suite; dependencies required. |
| `tests/test_build_intelligence.py` | Derived view construction, source-backed identity/MMR decisions, panel history, hot plans and complete rebuild. | Unit/fixture suite; dependencies required. |
| `tests/test_classify.py` | Category/keyword relevance, exclusions, roles, closing-time status and contact safety. | Unit/fixture suite; dependencies required. |
| `tests/test_cloud_collect.py` | Only unresolved sends reconcile before collection, with errors/not-found preserved rather than retried as new sends. | Unit/fixture suite; dependencies required. |
| `tests/test_cloud_daily_brief.py` | RFQ selection, editorial fields, restricted-access warnings, Sydney send window, cross-run send locks/gates and exact reconciliation. | Cross-platform fixtures; real flock cases Linux-only. |
| `tests/test_cloud_health.py` | Failed/hung current runs remain visible; mail identity, certificate, retention and morning/delivery grace checks. | Unit/fixture suite; dependencies required. |
| `tests/test_cloud_ops.py` | Validated atomic publication, rollback and restore integrity with preserved receipt/attempt/reconciliation state. | Cross-platform fixtures; atomic symlink publication/rollback cases Linux-only. |
| `tests/test_cloud_retention.py` | Protected pointers/readers, generation/backup/staging selection, safe roots/symlinks, dry-run and bounded storage behavior. | Cross-platform policy fixtures; real symlink/reader-retention cases Linux-only. |
| `tests/test_collectors_pending.py` | Stored-notice canary, durable unpublished-page retry/tombstone queue and explicitly empty ATM coverage. | Unit/fixture suite; dependencies required. |
| `tests/test_collectors_silent_output.py` | Invalid API envelopes, valid empty windows, watermark preservation, ACT empty lists and NSW pagination failures. | Unit/fixture suite; dependencies required. |
| `tests/test_collectors_unpublished.py` | API notices without public pages, duplicate-filter bypass and runner no-work versus failure classification. | Unit/fixture suite; dependencies required. |
| `tests/test_compliant_routes.py` | Robots-allowed notice/list/plan/panel routes, final-record skipping and explicit no-work reporting. | Unit/fixture suite; dependencies required. |
| `tests/test_crawl_integration.py` | Real Scrapy/middleware/pipeline/SQLite path against a local fixture HTTP service, including refresh. | Unit/fixture suite; dependencies required. |
| `tests/test_data_quality.py` | Placeholder phone removal, postcode state mapping, BuyICT text cleanup and standing-offer agencies. | Unit/fixture suite; dependencies required. |
| `tests/test_data_quality_alerts.py` | Row-count drop and unexpected-no-new-record alerts without false warnings for normal runs. | Unit/fixture suite; dependencies required. |
| `tests/test_export_views.py` | Tracker/brief/TODAY workbook columns and literal handling of note values. | Unit/fixture suite; dependencies required. |
| `tests/test_full_details.py` | Complete notice fields/raw details, contacts and repeatable schema additions. | Unit/fixture suite; dependencies required. |
| `tests/test_incremental.py` | New/updated/unchanged detection, URL migrations, mutable/final selection, coverage checks, throttling and recent-list limits. | Unit/fixture suite; dependencies required. |
| `tests/test_listings_pipeline.py` | Contact/date/phone/state/sector normalization, combined listings and no repeated sharing. | Unit/fixture suite; dependencies required. |
| `tests/test_mmr_list.py` | Dated original-list retention, ABN suggestions, workbook validation and persistent review decisions. | Unit/fixture suite; dependencies required. |
| `tests/test_outreach.py` | Manual queue/approval/send, cooldown/suppression/caps, dry-run and SMTP failure isolation. | Unit/fixture suite; dependencies required. |
| `tests/test_outreach_agent.py` | Legacy off switch/mode/caps, draft fact guards and fallback, inbox suppression/reply handling, greetings and business window. | Unit/fixture suite; dependencies required. |
| `tests/test_planned_procurement.py` | Plan-level contact inheritance and plans without contact boxes. | Unit/fixture suite; dependencies required. |
| `tests/test_prime_briefs.py` | Traceable claims, non-inflated MMR/rate arithmetic, explicit unknowns, escaping and closed-action/top-ten filtering. | Unit/fixture suite; dependencies required. |
| `tests/test_public_research.py` | Per-finding actual review dates survive later collection reviews. | Unit/fixture suite; dependencies required. |
| `tests/test_publish_engine_views.py` | Optional sheet handling, Tracker pull-before-write, read-failure protection, dropdowns and overdue action refresh. | Unit/fixture suite; dependencies required. |
| `tests/test_publish_sheets.py` | Stable registry reuse/recreation, blank-sheet claiming, folder validation, cell bounds and current/recent filters. | Unit/fixture suite; dependencies required. |
| `tests/test_releases.py` | Persistent feed start, source normalization, change hashes and effective closure. | Unit/fixture suite; dependencies required. |
| `tests/test_routine_coverage.py` | Supplemental research failures remain visible without silently changing mandatory send gates. | Unit/fixture suite; dependencies required. |
| `tests/test_routine_mail_config.py` | Private-file/environment precedence, address validation and absence of literal private mailboxes in routine source. | Unit/fixture suite; dependencies required. |
| `tests/test_routine_mail_env.py` | One-time extraction, no replacement, restrictive Linux permissions and reporting only key names. | Cross-platform extraction; chmod/chown case Linux-only. |
| `tests/test_routine_run_once.py` | Coordinator source/evidence gates with fake subprocesses and isolated paths. | Linux-only coordinator execution (`fcntl`); skipped on Windows. |
| `tests/test_routine_sender.py` | Production Gmail sender gates, attempt-before-send, identity/recipient checks, Sent reconciliation and uncertain-attempt refusal. | Unit/fixture suite; dependencies required. |
| `tests/test_snapshot.py` | Private/public archive exclusions/redaction, data-only restore and existing-database preservation. | Unit/fixture suite; dependencies required. |
| `tests/test_state_contracts.py` | WA resource mapping/unchanged skip, QLD header matching and date normalization. | Unit/fixture suite; dependencies required. |
| `tests/test_today_list.py` | Owned/dated/source-linked actions, formal live procurement channels and configured score thresholds. | Unit/fixture suite; dependencies required. |
| `tests/test_workflow.py` | Source schemas/upserts, pipeline rejection, metadata, mutable refresh and batch/report/timeout/CLI behavior. | Unit/fixture suite; dependencies required. |

For one file, use `python -m unittest discover -s tests -p "test_classify.py" -v`, changing the directory/pattern to match the table. Use discovery for `routine/scripts/test_sender.py`, rather than executing it as a mail command. `tests/test_routine_sender.py` stubs the Linux lock and Google modules, so it can inspect production send gates without real delivery.

Stage Linux verification on the server through the fixed CLI actions below. They upload a temporary test stage and run checks without installing changes; they are not local offline commands. None was run for this documentation task.

```powershell
python deploy\aws\illoura_aws.py verify-ops
python deploy\aws\illoura_aws.py verify-api
python deploy\aws\illoura_aws.py verify-collectors
python deploy\aws\illoura_aws.py verify-routine
```

Windows success cannot verify Linux flock, ownership, symlink publication or systemd behavior. Actual suite results, Linux deployment behavior, browser layouts and live OAuth/Sheets integrations are unverified here. Model benchmarks and held-out evaluation definitions are covered in [local-assistant.md](local-assistant.md), not silently run by unit discovery.
