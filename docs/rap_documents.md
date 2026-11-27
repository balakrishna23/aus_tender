# Own-site RAP document rules

The document pipeline retains publicly published RAP PDFs from organisations' own sites and extracts commitments and people without AI calls. RAP people are stored with `is_personal=true`; legacy extraction, register and directory-role policies are unchanged.

Run migration 026 before these commands or the updated research export:

```text
python -m market_intelligence rap-documents fetch --data-root ROOT --candidates candidates.csv
python -m market_intelligence rap-documents extract --data-root ROOT
python -m market_intelligence rap-documents report --output DIR
```

The CSV columns are `record_key,organisation,rap_type,period_as_published,url,found_via`. Use the organisation's own landing page or PDF URL. The Salesforce host is blocked before any request, including redirects. Each destination host's robots policy is checked before content requests. Robots failures fail closed except a 404. Redirects remain within the publisher's site. Requests are spaced by at least two seconds per host, including robots requests. PDFs must start with `%PDF` and fit within 40 MiB. Fetch audits are retained locally and in PostgreSQL; standalone acquisition audits are imported during extraction.

robots.txt redirects (for example apex to `www`) are followed up to five times, as RFC 9309 asks.

**Stealth browser fallback (`--stealth-fallback`).** Candidates the plain transport could not get (a bot wall: 401/403/429/5xx, an unreachable robots.txt, or a landing page with no PDF links) are retried once with headless Chromium initialised by `undetected_playwright`, as the browser collectors are. The same policy applies: robots.txt is re-read through the browser and checked for every URL, including where a navigation lands; the Salesforce host is aborted even as a subresource; the own-site boundary, two-second spacing and size cap stay. A robots.txt rule, a 404/410 and hosts under `qld.gov.au`, `wa.gov.au` and `data.gov.au` (standing no-stealth rule) are never retried. Fetching needs no database, so on the server it runs in the collectors' venv:

```text
PLAYWRIGHT_BROWSERS_PATH=/opt/aus_tender/.browsers PYTHONPATH=BUILD \
  /opt/aus_tender/.venv/bin/python -m market_intelligence.rap_documents --data-root ROOT --candidates candidates.csv --stealth-fallback
```

`extract` (research venv, migration 026) then imports the fetch audit files.

Extraction matches against the current RAP directory snapshot. Only matched documents enter `current_rap_document_findings`. Legacy approved extraction fields take precedence in `build_rap`; otherwise the report uses rule-based document fields with the PDF hash and source URL. Supply Nation membership alone never establishes a certification requirement. The absence of an explicit certification rule remains `unknown`.

Targets retain their complete normalised deliverable quote and page. A deliverable with both a percentage and a headcount has two target rows. For baseline-to-future numerical comparisons the final number is the target. These rules describe published commitments, not actual performance or available procurement budget.

Own-site discovery uses conservative domain boundaries for common publisher suffixes and known shared hosting services, without a network public-suffix lookup. Unrecognised country-code domain structures can lose sibling-subdomain coverage. OCR, scanned-only PDFs, spelled-out numeric targets, undated prose and unusual table layouts are not comprehensively extracted. Missing findings must not be interpreted as absence of a commitment.

## Sample evaluation, 28 November 2026

All three local samples matched their organisation, Stretch type and published years. Inputs remain private, uncommitted JSON page-text samples. No whole pages are reproduced here or in tests. Real contact addresses are retained in the ignored local report `.tmp/rap_sample_findings.json`; they are not printed here, following AGENTS.md. All extracted people rows are personal, including team contacts.

Counts below are target/commitment rows, not necessarily distinct deliverables.

| Sample | Detected period | Procurement | Employment | Supplier relationships | Supply Nation | Procurement strategy | Other | Total rows | People rows |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AECOM | March 2026 - March 2029 | 2 | 20 | 1 | 1 | 1 | 35 | 60 | 2 |
| Deloitte | September 2025 - May 2028 | 4 | 21 | 1 | 1 | 0 | 40 | 67 | 6 |
| NIAA | 2026-2029 | 0 | 5 | 0 | 0 | 0 | 3 | 8 | 4 |

AECOM: 60 distinct deliverables. Quantitative targets: 2% procurement spend; relationships with 40 businesses; 1% Australian staff base; 95% cultural learning, classified as other. The procurement spend and relationships timelines are September 2026, 2027, 2028, with responsibility Regional Procurement Operations Lead, ANZ. Employment has December 2026, 2027, 2028 and the First Nations employee-experience lead/support roles. Supply Nation membership and procurement strategy are captured separately. People: captured with roles and titles (contact block, CEO/sponsor/champion signatories); names, emails and phones are kept only in the database and the ignored local findings file.

Deloitte: 66 distinct deliverables, 67 rows because the employment goal has both 0.3% and 30 FTE targets; its timeline is May 2027, responsibility Director, Talent. The baseline 0.03% / 3 FTE is not selected as the goal. Other quantitative targets: AUD 500,000 minimum annual procurement spend, and 100 staff undertaking cultural learning (other). The dollar target timeline is May 2026, 2027, 2028, responsibility Director, Procurement. Supply Nation membership is captured. People: captured with roles and titles (contact block, CEO/sponsor/champion signatories); names, emails and phones are kept only in the database and the ignored local findings file.

NIAA: eight dated commitments, with quarter timelines recognised. No numeric targets are extracted; the sample contains no percentage or dollar literals. People: captured with roles and titles (contact block, CEO/sponsor/champion signatories); names, emails and phones are kept only in the database and the ignored local findings file.

## Verification

The requested uv commands were attempted with offline dependency resolution. The default shared cache lock was inaccessible. An isolated cache then could not resolve the pinned python-dotenv and FastAPI packages without network access. No packages were downloaded.

A fallback runner used already cached packages and blocked socket connections (allowing only Windows asyncio's private wakeup socketpair). Two pre-existing tests requiring loopback HTTP were skipped to honour the no-network instruction.

- New document tests: 17 passed, one PostgreSQL integration test skipped.
- Broad tests fallback: 1,170 run, 1,081 passed, 88 skipped, one environment error. The remaining error is the existing mobile-layout Playwright test: Windows denies creation of its subprocess pipe (`WinError 5`).
- API suite: unable to import FastAPI in the available offline environment.
- PostgreSQL integration is unverified because `ILLOURA_MARKET_TEST_DATABASE_URL` was not set. The opt-in test applies migrations and exercises extraction, idempotence, the view's JSON field shape and `build_rap` fallback.
- Changed Python files compile; CLI help and CSV/JSON report writing were checked.

No git commands, server operations, AWS operations, mail operations, timer changes or live-site requests were made.
