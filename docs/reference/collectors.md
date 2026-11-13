# Reference: collectors

Collect public procurement records, retain source evidence, and report whether each source completed.

Files covered:

- `run_spiders.py`
- `spiders/__init__.py`
- `spiders/austender_common.py`
- `spiders/aus_contract.py`
- `spiders/aus_tender.py`
- `spiders/planned_procurement.py`
- `spiders/standing_offer.py`
- `spiders/buy_ict.py`
- `spiders/act_tender.py`
- `austender_api.py`
- `nsw_tender_fetch.py`
- `settings.py`
- `pipelines.py`
- `middlewares.py`
- `duplicate_filter.py`
- `scrapy.cfg`

`run_spiders.py` runs eight sources sequentially: contract API, contract pages, tenders, plans, standing offers, BuyICT, ACT, NSW. The API must precede contract pages, which supply new panel links. A process lock prevents overlapping batches. `--spiders` selects sources; `--timeout`, `--retries`, and `--fail-fast` control failure handling; `--no-sync` suppresses Sheets writes; `--no-feed` skips downstream feed/intelligence work. `--backfill-since` supplies a historical start to supported collectors. Collection changes source SQLite databases; the downstream rebuild can still run after a partial collection, so a rebuilt feed alone does not prove complete coverage.

| Runner result | Meaning |
|---|---|
| `OK` | No detected process, crawl, database, or required sync failure. |
| `PARTIAL` | Process exited zero but stats, incomplete finish, logged errors, or sync show failure. |
| `FAIL(code)` | Child process failed or timed out. |
| `FAIL(no responses)` / `FAIL(no items)` | Scrapy produced no usable evidence without an explicit valid empty-work indication. |
| `crawl/nothing_due: 1` | Explicitly verified absence of work; bypasses missing-response/item checks, not other failures. |

Reports in `logs/runs/` hold per-source status, counts, timings, sync result and final `feed` text. They are written during the batch; the final `feed` field distinguishes finished downstream work. `logs/<source>.log` is rotated before collection. Archived logs retain 14 days; run reports retain 90 days. Quality warnings compare recent reports for a row-count drop over 30% or unexpectedly absent new records. `activity_log` records the batch; `dashboard` is rebuilt as a best-effort convenience. `HEALTHCHECK_URL` optionally receives start/success/failure pings. Overall failure returns exit code 1.

| Module/source | Site/API and records | Incremental rule and destination | Failure evidence |
|---|---|---|---|
| `austender_api.py` / `contract_api` | Official `api.tenders.gov.au/ocds` contract releases and amendments. | `contract_api.db`: append immutable `contract_versions`, rebuild touched `contracts`. `modified_through` advances only after collection succeeds; two-day overlap, initially seven days. `--history DATE` uses monthly checkpoints and rereads windows ending less than 62 days ago; `--active` covers future end-date years; `--ids` retrieves named contracts; `--rollup` derives contracts only. | Invalid HTTP-200 envelopes, malformed releases/links, transport failures and exhausted retries raise errors/nonzero exit. Valid empty releases can advance the watermark. |
| `spiders/aus_contract.py` | AusTender individual contract notice pages, including page-only panel, consultancy, contact and supplier fields. | API identifies missing notice IDs. `aus_contract.db/contracts`; `crawl_state.covered_through`, three-day overlap, initially 30 days. `scope=active_it` selects original notices for running ICT/labour-hire contracts; refetch modes fill missing fields. `pending_notices` retries unpublished pages outside the normal window; after 30 days rows remain as tombstones. | `crawl/extraction_failed`, `contract/page_not_found`, `contract/canary_ok`, database failures and retries. All unpublished pages can be valid no-work if the stored canary succeeds; without a canary the fallback cap is 25. An interrupted/mixed failing crawl is not valid no-work. |
| `spiders/aus_tender.py` | AusTender current ATM list and notice details. | `aus_tender.db/tenders`; refresh current notices and previously open notices that leave the list; `gone:<guid>` tracks departures. Saved closed notices are final. `since` cannot provide historical search because the archive search route is disallowed. | `crawl/extraction_failed`, `crawl/coverage_failed`; only an explicit empty marker or verified zero total can establish empty work. |
| `spiders/planned_procurement.py` | AusTender agency list and annual procurement plans. | Revisit agency plans and details into `planned_procurement.db/planned_procurements`; plan-level contact is used where the row refers to it. Publication filtering uses the latest crawl rather than deleting history. | `crawl/extraction_failed` on missing/changed extraction, plus shared retry/database signals. Plans that return no rows without an explicit empty statement are counted (`crawl/planned_unexplained_empty_plans`, `crawl/planned_unexplained_empty_pages`) and can set `crawl/planned_extraction_failed`; paging that cannot be confirmed is counted as `crawl/planned_pagination_unverified` / `crawl/planned_pagination_warnings`. |
| `spiders/standing_offer.py` | AusTender standing offer pages linked from collected contract notices. | New panel links plus current offers whose copy is older than seven days; `standing_offer.db/standing_offers`. Unknown end dates remain current for rechecking; expired offers remain history. `refetch=missing_details` supports older incomplete records. | `crawl/nothing_due` if neither new nor due; malformed details and shared crawl/database failures. |
| `spiders/buy_ict.py` | Public BuyICT ServiceNow page/widget calls, listing and RFQ details. | `buy_ict.db/buy_ict_tenders`; walk listing pages, update listing facts, fetch new/open details and one final copy after closure. Closed records already saved closed skip detail fetch. | `crawl/extraction_failed` for missing payload/details or incomplete pages. Reported totals include seller-only records, so coverage means every public listing page, not equality with the total. When the page gives no total (`crawl/buyict_unknown_totals`) the spider follows the fallback next-page fields and records `crawl/buyict_pagination_failed` if the walk stops without an explicit end (field names unverified against the live site). |
| `spiders/act_tender.py` | ACT open and recently closed tender listing tables via Playwright. | Refresh listing-derived rows in `act_tender.db/act_tenders`; no durable date watermark. Extracts listing fields without visiting challenged detail pages. | `crawl/extraction_failed`; `crawl/act_empty_lists` records explicit empty lists; both expected lists must be explicitly empty for `crawl/nothing_due`. Rows are compared with the total the page states: `crawl/act_pagination_failed` on a mismatch, `crawl/act_pagination_unverified` when no total is shown. |
| `nsw_tender_fetch.py` / `nsw_tender` | Buy NSW notices and public opportunity details via standalone asynchronous Playwright. | Upserts `nsw_tender.db/nsw_tenders`; `--pages` caps notice pagination (runner uses five). Missing open opportunities are closed only after complete listing coverage. No durable date watermark. | Exceptions/nonzero exit and emitted `crawl/nsw_notice_coverage_failed`, `crawl/nsw_opportunity_coverage_failed`; the runner reads these stats from log text even though this is not a Scrapy spider. Details of known open tenders older than 7 days are refreshed, at most 20 a run (`detail_refreshed_at`; `crawl/nsw_details_refreshed`, `crawl/nsw_detail_refresh_errors`). |

| Supporting module | Responsibility |
|---|---|
| `spiders/austender_common.py` | Notice IDs, date parsing/windows, stored-record lookup, missing-detail refetch and listing totals. Tracks `crawl/items_unchanged`, extraction and coverage failures. Retains shared recent-list/search helpers; their presence does not mean current spiders use disallowed search routes. |
| `spiders/__init__.py` | Package marker; no collection logic. |
| `settings.py` | Loads `.env`; enables robots obedience, throttling, database duplicate filtering, cleaning/storage pipelines, per-domain concurrency limits and optional Sheets sync. Relative credential paths resolve beside the project. |
| `scrapy.cfg` | Selects the root `settings` module. |
| `duplicate_filter.py` | Combines Scrapy fingerprints with database URL checks. Detail refresh metadata bypasses persistent skipping where source records can change. |
| `pipelines.py` | Cleans values, validates required identity fields and stores accepted items. Counts `database/items_saved`, `database/items_failed`, and `database/items_new`, `updated`, `unchanged`; optional sync runs when the spider closes. Field-loss check: per source it counts items missing a key field (`KEY_FIELDS`; `crawl/field_loss_items_checked`, `crawl/field_loss_items_missing`, `crawl/field_loss_missing/<field>`) and sets `crawl/field_loss_failed` when at least 10 items were seen and more than half lack one. |
| `middlewares.py` | Injects spider identity into request metadata; on HTTP 429/503 applies domain backoff, respecting retry delay, before Scrapy retries. |

Common failure stats include `httperror/response_ignored_count`, `retry/max_reached`, `spider_exceptions/*`, `crawl/*_failed`, and `database/items_failed`. Non-`finished` or absent `finish_reason` is incomplete. `crawl/items_unchanged` and persistent duplicates can explain zero newly scraped items. Standalone sources use process/log evidence rather than Scrapy response/item counts. Live portal coverage is unverified in this static review.
