# Tender Feed → Illoura IT Revenue Intelligence: what is reused, changed and added

Week 0 deliverable (spec section 2). Status as of 7 October 2026. Illoura confidential.

## Corrections to the spec's assumptions (section 2)

| # | Spec assumed | Actually |
|---|---|---|
| 1 | Runs every 6 hours to a database and Google Sheets | Built that way (Windows Task Scheduler, SQLite, Google Sheets). The scheduled tasks are **paused**; runs are started by hand until the prototype is approved. |
| 2 | Contract notices: 31,660 | The website scraper held 32,157 (2025–26, with gaps). Now replaced as the source by AusTender's official API: **424,812 contracts, 590,015 versions, 2020→now** plus older contracts still running. |
| 2 | Tenders ACT: 92, contact currently empty | 94 records; contacts still empty: Tenders ACT's robots.txt disallows the detail pages that hold them (`/tender/view`). Needs the Tenders ACT export or another permitted route. |
| 2 | buy.nsw: 247 | 265; the fetch searched healthcare and recruitment only. Now IT, recruitment and healthcare. |
| 3 | "Limited Tender Exemption" field | Confirmed, from the API: 3,217 Appendix A item 16 contracts. |
| 4 | BuyICT IDs appear in some contract descriptions | 207 distinct BuyICT IDs found in contract descriptions, 100% of which exist in BuyICT; 368 contracts linked. |
| 5 | UNSPSC codes from AusTender and buy.nsw | AusTender: yes (API and ATM/plan categories). buy.nsw: category names only, so a category-name rule is used. |
| 6 | Outreach can be switched off on its own | Yes: sending is switched off in code; nothing else depends on it. |
| 7 | Each source's terms allow collection | AusTender: the API is CC BY 3.0 AU; robots.txt disallows `/Search/`, which contracts, tenders and panels no longer use. **Planned procurements still need `/Search/`** (no allowed list). Tenders ACT disallows its detail pages (not used). |

## Reused as is

BuyICT spider (its API); buy.nsw fetch; Tenders ACT listing; SQLite storage with automatic column
migration; Google Sheets publishing; backups; run reports; activity log; tests and CI.

## Changed

| What | Change |
|---|---|
| Contract notices | Listed by the API; only each notice's own page is opened for SON ID, ATM ID and other page-only fields (`/Cn/Show`, allowed). The old search route also missed about half of all amendments. |
| Approaches to market | From the Current ATM list (`/Atm`, allowed), not `/Search/`. |
| Standing offers | Found through the SON links on contract notices; current panels re-checked weekly. |
| Contract categories | UNSPSC families moved to `config/categories.yaml` and widened (4320–4322): 21,386 more IT contracts, e.g. DFAT "IT Support" ($32.2m). |
| buy.nsw | IT categories added; ICT keywords. |
| Outreach agent | Autonomous sending off (spec Week 0 item 1). |
| Backups | The 2.6 GB contract database is copied weekly, not daily. |

## Added

| Spec | Built |
|---|---|
| 3.1 real status | `status_effective` from the closing time (Canberra); the portal's value kept as `status_source`. |
| 3.3 relevance | Category code → category name → title keywords (strong / weak) → exclude; `vertical`, `relevance_reason`, `needs_review` for unclear titles. |
| 3.4 exemption | `is_indigenous_exemption`, exemption-use view. |
| 3.5 one record per contract | API versions rolled up: original/current value and end, amendments, extensions. |
| 3.6 BuyICT → contracts | `buyict_links` with an estimated day rate (value ÷ ACT business days, labelled). |
| 3.7 codes and ABNs | UNSPSC per contract; panel members with ABNs. |
| 4 organisation master | ABN → normalised name → alias table → review queue (never merged automatically). |
| 5 NIAA MMR list | Loaded as received (`mmr_list.py`), matched to ABNs, review decisions kept. |
| 5 panel watch | `config/panels.yaml`; members with contract counts. |
| 6 engines 1, 2, 3 (steps 1–5), 4, 5 (rates) | `databases/intelligence.db` views. |
| 7 scoring and TODAY | `today_list.py`: rules-only draft, weights from `config/scoring.yaml`. |
| 8 contact safety | FORMAL_CHANNEL_ONLY while live, SAFE_GENERAL otherwise, NO_CONTACT for opt-outs. |
| 11 data quality | Alert on a >30% row-count drop or a source bringing nothing new. |

## Not built yet (needs Illoura's inputs or a decision)

- Bench / ATS capability (delivery readiness), relationships and warm paths (engine 6), RAP table,
  commercial assumptions (engine 8), portfolio Indigenous targets and the agency → portfolio map.
- Tenders ACT contacts (needs the export file).
- Planned procurements without `/Search/` (decision).
- AI classification of unclear titles (rules leave ~10 open items for a person or AI).
- TODAY screen (Sprint 3); Sheets tab is ready once a blank sheet exists in the folder.
