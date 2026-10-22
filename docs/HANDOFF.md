# Handoff: Illoura IT Revenue Intelligence (Tender Feed) — state at 7 Oct 2026, 20:50 (Sydney)

> Setting up a new PC or running the project day to day? See [SETUP_AND_OPERATIONS.md](SETUP_AND_OPERATIONS.md).
> This file is the engineering history and decision log; parts of it (paths, test counts) date from 7 Oct.

## Local assistant update — 8 October 2026

Sheets follow-up: pilot 2.3 adds a read-only import of the 10 registered project
workbooks into private `databases/sheet_imports.db`. Initial import: 26 tabs,
51,637 rows, no failed reads. The browser has a manual Sync Google Sheets button
and optional imported Sheet context. Exact row/tab lookups include provenance;
snapshots retain history and failures retain the previous active tab. Generic
Sheet arithmetic, Sheet edit history and semantic Sheet-row indexing are not
implemented. Source databases and Google Sheet cells are unchanged. Assistant
unit checks now total 83; see the README for prompt examples and sync limits.

Hybrid retrieval follow-up: pilot 2.2 now has CPU `all-minilm:22m` embeddings,
BM25 and reciprocal rank fusion over SQL-filtered records. The first private index
contains 5,840 unique chunks from renewals, relevant current opportunities and
reviewed research findings. Full historical contracts, RAP PDFs and CVs are outside
this first index. Run `local_assistant/build_hybrid_index.py` after refreshes; missing
chunks/model outages cause a labelled keyword fallback. See the assistant README.

- Added `local_assistant/`: browser UI, read-only SQLite query coordinator, validated
  rules/Qwen planning, deterministic evidence formatting and optional official API
  checks for up to three selected contracts. No model-generated SQL or facts.
- Base model: `qwen3:4b-instruct`, with `illoura-planner:latest` prompt/profile.
  Weights were not trained. Ollama binaries and model files are not committed.
- Manual refresh completed; the standing-offer batch had no pages due, followed by
  a separate successful 457-page current/undated recheck and intelligence rebuild.
- Validation: 54 assistant unit checks, 90 question regression checks, 195 upstream
  tests, plus desktop/mobile and HTTP checks. Question cases were used in refinement;
  these are not independent accuracy estimates.
- Databases, private research/bench/relationship inputs, reports, logs and backups
  remain excluded from GitHub. No outreach, Sheets publishing, VPS deployment or
  scheduler enablement. Candidate matching, memory and general web search remain gaps.
- See `local_assistant/README.md` for checkout setup and limitations. Run assistant
  fixtures with `python -m unittest discover -s local_assistant -p "test_*.py"`.

You are picking up a working Python project mid-build. Read this whole file before changing
anything. It is written so you can continue exactly where the previous engineer (Claude) stopped,
without re-deriving decisions. Where this file and the code disagree, trust the code and the tests,
then fix this file.

---

## 0. Start here (10-minute checklist)

1. Work in `C:\work\aus_tender` (Windows 11). Python is
   `C:\Users\merug\AppData\Local\Programs\Python\Python311\python.exe` (3.11). There is no venv.
2. Run the tests: `python -m unittest discover -s tests` → expect **180 tests, OK**. (One log line
   "[pipelines] ERROR: ... Failed to save item for aus_tender" is an existing *negative* test; ignore it.)
   pytest is not installed; use unittest.
3. Git: your branch is `main`, tracking remote **`github`** = `https://github.com/balakrishna23/aus_tender`
   (private). **Ignore the remote called `origin`** (git@github.com:992panda/aus_tender)
   and the local branch `main-old-history`: that is an older, unrelated history. Always `git push github main`.
   CI (GitHub Actions, `.github/workflows/tests.yml`) runs the tests on Ubuntu and Windows on every push.
4. Do the two unfinished tasks in section 9 (Tracker tab sync, then prime briefs), in that order.
5. Never print or commit secrets (section 2). Never send emails (section 2).

---

## 1. What this is and who it is for

**The company:** Illoura Services, an Indigenous-owned business supplying IT people and services to
Australian government. Founders: **Lane** (founder: prime and partner relationships) and **Sirish**
(co-founder: agency relationships; Sirish is the person you are working for). There is **no Team Lead**:
it is just Sirish and the engineer. Everything the spec assigns to a "Team Lead" (config files,
sign-offs) is drafted by the engineer and approved by Sirish.

**The spec:** "Illoura IT Revenue Intelligence — Spec for the AI Engineer" (PDF, 23 pages, 7 Oct 2026,
in Sirish's Downloads; not in the repo — it is confidential). Its essence is reproduced in section 4.
A second PDF (`..._Requirements_v2_1_Final.pdf`) exists: **Sirish said not to read it.**

**The goal, in one line:** turn public government purchasing data into a short daily list (TODAY) of
evidence-backed, owned, dated actions telling Illoura's IT team whom to talk to and why, so it wins more
government IT work (as a prime or as a subcontractor to big primes).

**How Sirish likes to work (important):**
- He is not technical about government websites. Explain in plain language, with real examples from
  the data, short tables, and numbers. Avoid jargon or define it.
- He wants progress reports and honest status ("what's done, what's left, what's blocking").
- Discuss before large builds or long downloads; small fixes can just be done.
- He asked for a working prototype first; **no VPS until the prototype gives the results he wants.**
- He prefers manual runs for now: **the Windows scheduled tasks are disabled** (section 7). Don't
  re-enable them unless he says so.

---

## 2. Hard rules (do not break)

1. **No autonomous emails, calls or messages.** AI may draft; a person approves and sends. The outreach
   agent's sending is switched off in code (`outreach_agent.SENDING_ALLOWED = False`). Never turn it on.
   Never contact a buyer whose tender is live (contact safety, section 4.6).
2. **Secrets:** `.env` and the Google service-account key `dazzling-tiger-470805-*.json` (repo root) are
   git-ignored. Never print, log, paste or commit their contents. `.env.example` lists the variable
   names. The git history of `github/main` was scanned: no keys in it.
3. **Respect each site's terms.** `settings.py` has `ROBOTSTXT_OBEY = True`. No spider uses
   tenders.gov.au `/Search/`, `/Reports/`, `/Cn/List`, `/Son/List` (disallowed). Tenders ACT disallows
   `/tender/view`, `/contract/view`: do not fetch those, and do not try to get past Cloudflare
   (that would be evasion). No LinkedIn scraping, no guessed emails, no bought lists.
4. **Raw data is never overwritten**; derived data is stored separately (`intelligence.db`).
5. **AI must never invent** a person, email, value, rate, clearance, relationship or availability, and
   must never present an estimate as fact (rates are always labelled "estimate").
6. **Confidential:** the spec, config, research and code are Illoura's IP (spec section 13). Business
   inputs live in `inputs/` (git-ignored). Exports (`exports/`), databases (`databases/`), logs are
   git-ignored.
7. **Tooling pitfall on this machine:** Python code sent through a bash heredoc turns `\b` into a
   backspace byte (0x08) in the file, silently breaking regexes. Write code with your file-editing tool,
   or run a saved script. After any scripted edit: `grep -c $'\x08' file` must print 0.
   Also: in Git Bash, `MSYS_NO_PATHCONV=1` makes curl write `-o /tmp/x` to the wrong place.

---

## 3. Architecture

### 3.1 Sources and how each is read (all routes allowed by robots.txt)

| Source | Code | Route | Database / table |
|---|---|---|---|
| AusTender contract notices — official API (OCDS, CC BY 3.0 AU, credit AusTender) | `austender_api.py` | `https://api.tenders.gov.au/ocds/findByDates/{contractPublished|contractLastModified|contractStart|contractEnd}/{from}/{to}`, `findById/{CN}`; 100 releases/page, follow `links.next` | `contract_api.db`: `contract_versions` (one row per notice/amendment, raw JSON, never overwritten), `contracts` (rolled up, rebuilt) |
| Contract notice pages (fields the API lacks) | `spiders/aus_contract.py` | notice list comes from `contract_api.db`; only `/Cn/Show/<guid>` pages are opened. guid = 32-hex suffix of `contracts[0].items[0].id` (verified 32,157/32,157) | `aus_contract.db` `contracts` (SON ID + `son_url`, ATM ID, consultancy, confidentiality, suppliers invited, extension options, max end date, office postcode) |
| Approaches to market (open tenders) | `spiders/aus_tender.py` | Current ATM list `/Atm` (15/page, "of N records"); open ATMs re-fetched every run; a stored open ATM no longer listed is opened once (redirects to `/Atm/ShowClosed/`); withdrawn pages remembered as `crawl_state` key `gone:<guid>` | `aus_tender.db` `tenders` |
| Planned procurements | `spiders/planned_procurement.py` | `/App/List?Tab=List By Agency` (143 agency plans) → `/App/Show/<plan>` (15 items/page); item URL `/App/Show/<plan>?ppId=<id>#pp-<id>`; "As for APP" contact filled from the plan's contact box | `planned_procurement.db` `planned_procurements` (rows not re-seen within a day of the newest `scraped_at` are no longer listed) |
| Standing offers (panels) | `spiders/standing_offer.py` | new SONs from `aus_contract.contracts.son_url`; stored current SONs re-checked if older than 7 days (`/Son/Show/<guid>`) | `standing_offer.db` `standing_offers` (`current_suppliers` JSON with ABNs) |
| BuyICT (Commonwealth ICT marketplace; LH-/PCS-/RFI- requests) | `spiders/buy_ict.py` | BuyICT's public ServiceNow API; robots.txt empty | `buy_ict.db` `buy_ict_tenders` |
| Tenders ACT | `spiders/act_tender.py` | `/tenders/open`, `/tenders/closed` lists only; **no contacts** (they are only on disallowed detail pages) | `act_tender.db` `act_tenders` |
| buy.nsw | `nsw_tender_fetch.py` (Playwright, standalone) | search with categories `information-and-technology,recruitment-services,healthcare` | `nsw_tender.db` `nsw_tenders` |
| NIAA "IPP companies with active MMR contracts" | `mmr_list.py` | manual download from https://www.niaa.gov.au/resource-centre/ipp-companies-active-mmr-contracts-0 (current file is "as at 25 Feb 2026", byte-identical to `inputs/mmr/ipp-companies-active-mmr-contracts-0.xlsx`) | `organisations.db`: `mmr_list` (as received, per list_date, never overwritten), `mmr_matches` (company → supplier ABN suggestions + review decisions) |

### 3.2 Batch run (`run_spiders.py`)

Order (`DEFAULT_SPIDERS`): `contract_api` → `aus_contract` → `aus_tender` → `planned_procurement` →
`standing_offer` → `buy_ict` → `act_tender` → `nsw_tender`. Then: data-quality warnings
(`data_quality_warnings`: row count down >30%, or nothing new where usually ≥10) → `build_releases`
(new-releases feed) → **`build_intelligence.main([])`** → `publish_sheets.publish_all()` (if
`GSHEETS_FOLDER_ID` set and not `--no-sync`) → `dashboard.build()`. Reports in `logs/runs/<time>.json`.
Typical full run ≈ 30 min.

Commands:
```
python run_spiders.py --timeout 14400 --retries 1          # a full refresh (what the scheduled task ran)
python run_spiders.py --spiders contract_api aus_contract   # selected sources
python austender_api.py                                     # contract API incremental (modified since last run, 2-day overlap)
python austender_api.py --history 2020-01-01 --active       # full history (done; ~6 h; resumable by month)
python austender_api.py --ids CN123,CN456                   # named contracts, all versions
python -m scrapy crawl aus_contract -a since=2026-10-01     # notice pages for notices published since
python -m scrapy crawl aus_contract -a scope=active_it      # page fields for running ICT-services/labour-hire contracts
python build_intelligence.py                                # rebuild engine views (~17 s)
python export_views.py                                      # exports/illoura_views_<date>.xlsx
python publish_sheets.py --only intelligence                # push engine views to the Google Sheet
python mmr_list.py --load <xlsx> --date YYYY-MM-DD | --match | --export <csv> | --review <csv>
python backup_databases.py --keep 14                        # daily copies; contract_api.db weekly (keep 2)
```

### 3.3 Engine views (`build_intelligence.py` → `databases/intelligence.db`, rebuilt every run)

Written to a `.partial` file then swapped in. Tables:

| Table | What | Engine |
|---|---|---|
| `opportunities` | every ATM, BuyICT request, ACT, NSW (non-award) notice and *still-listed* plan: `status_source`, `status_effective` (Closed once closing time in Canberra has passed), `vertical`, `is_relevant`, `needs_review` (unclear title), `relevance_reason`, `category_family`, `role_families`, `contact_safety`, `url`, `retrieved_at` | 1 |
| `hot_list` | planned ICT procurements expected this quarter or the next two, with a contact (13 today) | 1 |
| `renewals` | ICT/labour-hire contracts ending within 365 days, window 0-30/31-60/61-90/91-180/181-365, `supplier_on_mmr_list`, agency contact, `page_url` | 2 |
| `mmr_primes` | NIAA companies → ABN, `mmr_contracts`, `group_name`, `group_mmr_contracts` (groups from config: DXC = 5 + 1 = 6), contract counts | 3 |
| `mmr_candidates` | their active contracts ≥ $7.5m, labelled "candidate (public data does not confirm an MMR)" | 3 |
| `panel_members` | members of the watched panels with ABN, contract counts, `panel_contracts` (from SON IDs on notice pages) | 3 |
| `exemption_use` | Appendix A item 16 contracts (direct purchase from Indigenous SMEs) | 4 |
| `nsw_small_contracts` | NSW ICT awards ≤ $250,000 | 4 |
| `buyict_links` | BuyICT request IDs found in contract descriptions → contracts, `estimated_day_rate` = value ÷ ACT business days (labelled estimate) | 5 |
| `agency_contacts` | seed contacts (High) + latest contract-notice contact per agency/address (Medium), `verified`, `stale` (>18 months), `opted_out`, `contact_safety`, `usable_for_outreach` | 6 |
| `organisations`, `organisation_names`, `organisation_review` | organisation master: (1) ABN, (2) normalised name, (3) alias table, (4) close names → review queue, never merged | §4 |
| `actions`, `today` | all proposed actions (scored) and the TODAY selection (`today_list.py`, then `action_tracker.apply_to_today`) | 7 |
| `build_info` | build time, BuyICT link stats | — |

### 3.4 Config (`config/*.yaml`, loaded by `classify.config(name)`; all are DRAFTS awaiting Sirish's approval)

- `categories.yaml`: `families` (UNSPSC prefix → family; spec's 801116/801117/8111/8116/4323 **plus 4320/4321/4322** "ICT equipment and general" — this caught 21,386 more contracts incl. DFAT "IT Support" $32.2m), `ict_category_names`, `strong_keywords`, `weak_keywords` (two different weak words = relevant; one = unclear → `needs_review`), `exclude_keywords` (override), `role_families`, `relevant_role_families`. Keywords match **titles only** (descriptions caused false positives).
- `panels.yaml`: SON4102906 Digital Marketplace Panel 2; SON3897769, SON3965020, SON4241474 People Panel phases; SON3520191 Defence ICT Provider Arrangement; SON3538332 Capability Support Services Panel.
- `procurement_rules.yaml`: Mandatory Set-Aside $80k–$200k; Exemption 16; MMR ≥ $7.5m (4% contract-based / 3% organisation-based); ACT single quote < $200k; NSW direct engagement ≤ $250k.
- `scoring.yaml`: weights attractiveness 20, illoura_fit 20, access 20, timing 15, delivery_readiness 15, commercial_potential 10; thresholds ACT NOW 85, INVESTIGATE 70, WATCH 50, else IGNORE; TODAY 5–15 items; renewal windows; stale after 18 months.
- `organisation_aliases.yaml`: aliases (DXC variants → ABN 18008476944; Digital Canberra → City and Environment Directorate) and `groups` (DXC Technology (group) = DXC TECHNOLOGY AUSTRALIA PTY LIMITED + DXC ENTERPRISE AUSTRALIA PTY LTD).
- `seed_contacts.yaml`: the spec's 13 published procurement contacts (High confidence).
- `opt_outs.yaml`: permanent opt-outs (empty); live-mode unsubscribes from `outreach_agent.db` `suppressed` are also applied.

### 3.5 Other modules

- `classify.py`: `classify(title, description, category, code, source_is_ict)`, `family(code)`,
  `closing_time(text)` (handles "Thursday, 22 October 2026 – 11:59pm, Canberra time", "9-Nov-2026 12:00 pm",
  "Tue, 03 November 2026 2:00 pm", dates alone → 23:59), `effective_status`, `contact_safety`.
- `today_list.py`: action templates — `prime_actions` (Tier-2 offer to a NIAA-listed prime with a
  staffing/IT-services renewal ≤180 days; one per company/group; hook = best-timed renewal; owner Lane),
  `agency_actions` (agencies with ≥3 Exemption-16 ICT/LH contracts in 2 years and renewals; owner Sirish),
  `plan_actions` (hot list → register interest; Sirish), `live_actions` (live ICT items → formal channel
  only; Recruiter). `score()` uses config weights. Delivery readiness and warm access are **neutral 0.5**
  (no bench / relationships data yet) and every row says so in `assumptions`. `build()` picks the best of
  each action type first, then by score, max 15, cap per type.
- `action_tracker.py` (new, section 9.1): `databases/actions.db` (`actions`, `action_history`).
- `mmr_list.py`: NIAA list loader/matcher (section 3.1).
- `publish_sheets.py`: one Google Spreadsheet per dataset; registry `databases/sheets_registry.json`
  (keys: releases, tenders, buyict, nsw, act, planned, contracts, standing_offers, outreach,
  **intelligence**). The service account cannot create files in a My Drive folder: it "claims" a blank
  sheet a person made in the folder. The intelligence sheet ("Illoura - Engine Views") was made by Sirish
  *outside* the folder and registered by hand. Tabs not in a dataset's dict are left alone by
  `publish()`; a tab named in the dict is fully replaced (staged copy, atomic swap).
  `OPTIONAL = {'intelligence'}`: "no spare spreadsheet" is a skip, not a failure.
- `export_views.py`: Excel workbook of the views for review.
- `outreach_agent.py`, `outreach.py`: old autonomous email agent (Groq writer). Sending OFF. To be
  rebuilt as "AI drafts, person approves" under contact-safety rules. Its `run(dry_run=True)` still drafts.
- `backup_databases.py`, `activity_log.py`, `dashboard.py`, `build_releases.py`, `listings_pipeline.py`,
  `gsheets_sync.py`, `sheets_writer.py`, `database_manager.py` (SCHEMAS + automatic column migration),
  `project_paths.py` (`AUSTENDER_DB_DIR` env var overrides the databases folder; tests use it).
- Docs: `README.md` (long; sections per component), `docs/spec_mapping.md` (spec Week-0 "reuse/change/add" page).

---

## 4. The spec, condensed (what "done" means)

### 4.1 Definition of done
For any priority organisation, a founder understands in five minutes: why to target it, what work exists,
who holds it today, the pathway, who to speak to, what to offer, whether Illoura can deliver, the next
action, its owner and follow-up date, and what happened afterwards. "Five-minute test."

### 4.2 Eight engines (collect → clean/link → rules first, AI only where rules can't decide, storing its
reason → score → output to a Sheet tab now, a screen later; source link on every row)
1. **Opportunity** — where new ICT work emerges (BuyICT, ATMs, planned procurements, ACT, NSW); classify
   ICT; role family (cyber, AI/data, architecture, delivery, analysis, engineering, testing, platforms);
   link buyer; contact safety; score. Planned-procurement hot list (register interest; contacts are published so suppliers can prepare).
2. **Renewal** — ICT/labour-hire contracts by days to end; incumbent + agency contact. Lane offers the
   incumbent prime Illoura contractors as a Tier-2 partner.
3. **Prime and Tier-2 (MMR)** — MMRs: Commonwealth contracts ≥ $7.5m in specified sectors need ~4% Indigenous
   participation (contract-based) or 3% (organisation-based); Illoura spend counts. Steps for a prime:
   1 Load NIAA list; 2 Match to org master; 3 Candidates = active ≥ $7.5m (labelled candidate); 4 Sector check;
   5 Tier-2 signals (labour-hire contracts, panels); 6 RAP facts (from a manual RAP table); 7 People (engine 6);
   8 Capability (Illoura's bench); 9 Score. Output: **one-page brief for Lane** (see 9.2). Tier-2 targets:
   panel members with contract and MMR counts (Sirish calls their Canberra practice managers).
4. **Indigenous pathways** — agency propensity (Indigenous ICT contracts last 24 months, value, recency,
   Exemption 16 use, renewals/plans, relationship). Rules in `procurement_rules.yaml`. Never imply automatic award.
5. **Rate, demand and competitor** — BuyICT joined to contracts: by agency and role family, demand over
   30/90/180/365 days, clearance mix, median and range of estimated daily rates, top winners, incumbents on expiring work.
6. **People and relationships** — 2–4 people per priority organisation with confidence + warm path
   (e.g. Lane → mutual connection → DXC social impact lead). Every person: source, verified date, confidence;
   STALE after 18 months; opt-outs suppress everywhere. Manual sources only for prime contacts.
7. **Action** — each signal type → action template; check contact safety; AI drafts the reason from stored
   facts; owner + due date; overdue items come back. Output: TODAY.
8. **Commercial** — per pursued account: positions, assumed margin and hours (founders' config), stage
   probability, weighted contribution; founders-only view vs their target (e.g. 10 contractors × $20/h × 1,800 h = $360k/yr).

### 4.3 TODAY: 5–15 ranked actions, each with why now, evidence, target person, offer, capability match, owner,
due date, desired outcome. Sheet tab in Sprint 1, simple screen in Sprint 3.

### 4.4 Delivery plan: Week 0 + three 4-week sprints. Sprint 1: data fixes (§3), org master (§4), engines 1, 2, 7
(Sheet). Sprint 2: new sources (§5), engines 3, 4, 6. Sprint 3: engines 5, 8, TODAY screen, quality (§11).
Scope guard if slipping (keep in order): contact safety + source links; clean data; renewals/contacts/hot list;
prime briefs + pathways; people + warm paths; actions + follow-ups. Defer first: screens, rate estimates, commercial view.

### 4.5 Quality (§11): credentials out of code; outputs shared only with named staff; only business contact details;
unused contacts deleted after 24 months; retries; one failed source never blocks others; reruns never duplicate;
daily backups (restore tested quarterly); row counts per source after every run with alert on >30% drop; monthly
20-record accuracy check per view; every view shows its last refresh; explainable scores; config not code;
`vertical = ICT` on every record; monthly running cost recorded; README, data dictionary, runbook, changelog;
one automated test per checklist item using the real test files.
AI rules: may classify relevance/role family, extract facts, summarise, draft for approval, analyse; must never
invent people/values/rates/clearances/relationships/availability/pathways, present estimates as facts, hide
uncertainty, or contact anyone. Each AI task keeps its prompt version and a small labelled test set.

### 4.6 Contact safety (§8): planned procurement not yet released → SAFE_GENERAL (register interest);
live tender/RFQ/RFI → FORMAL_CHANNEL_ONLY (formal Q&A only); closed tender or contract notice → SAFE_GENERAL
(never imply an award can change); prime/partner with no live procurement → SAFE_GENERAL; opt-out → NO_CONTACT.
Every email names the sender and has an opt-out line.

### 4.7 Sign-off checklist (§12) and current status (24 items)

| # | Test | Status |
|---|---|---|
| W0 | Open-tender email trigger off | ✅ |
| W0 | Mapping shared; config loaded; outputs restricted to named staff | 🟡 config are drafts; check sheet sharing |
| S1 | No record Open with past closing date (PICI0008792 Closed) | ✅ |
| S1 | ≥90% ACT records have a contact email; 10 match live page | ❌ blocked (robots.txt); needs Tenders ACT export |
| S1 | ACT titles in 3.3 classify correctly | ✅ (`tests/test_classify.py`) |
| S1 | CN3695219 one record: $427,580 → $2,558,490.39, ends 5 May 2023, 5 amendments, "App A: 14" | ✅ |
| S1 | DXC variants → ABN 18 008 476 944 | ✅ |
| S1 | Agency contacts show seed contacts with correct contact safety | ✅ |
| S1 | APRA/DXC (CN4226826, ends 30 Nov 2026) in 31–60 window | ✅ (54 days on 7 Oct) |
| S1 | Treasury LH-08047, LH-08049, PCS-04608 never informal | ✅ |
| S1 | Every item has source link + retrieval date | ✅ |
| S2 | DXC NIAA MMR count 6 + DFAT IT support $31.4m candidate (CN4262189, $31.35m → $32.2m) | ✅ |
| S2 | Tier-2: members of SON4102906 and SON3965020 with contract counts | ✅ |
| S2 | Exemption-use view; 10 random rows verified | 🟡 view done; manual verification not done |
| S2 | Hot list ICT, current + next two quarters, with contacts | ✅ |
| S2 | NSW ICT records ≤ $250k | ✅ (5 rows; grows as NSW runs with IT categories) |
| S2 | Every person: source, date, confidence; STALE; opt-outs | ✅ for agency contacts |
| S2 | Three prototype cases (an agency, DXC, an Indigenous pathway) answer the Definition of done | 🟡 needs people, capability, brief |
| S3 | Every priority item → owned, dated action; overdue follow-ups appear in TODAY | 🟡 logic done (9.1); sheet sync not wired |
| S3 | LH-06470 → DEWR $154,440 contract (CN4259794, Macrotech, ends 31 Dec 2026) with rate as estimate | ✅ (~$1,188/day) — note LH-06470 has **11** contracts, 9 suppliers |
| S3 | Pipeline stages and win/loss reasons recorded | 🟡 fields exist in tracker; sheet sync not wired |
| S3 | Commercial assumptions visible; target founders-only | ❌ |
| S3 | Data-quality alerts + monthly accuracy check | 🟡 alerts done; monthly check not built |
| S3 | The week in §9 runs for one full week | ❌ needs scheduling turned back on |

Overall ≈ 60–65% of the spec. Week 0 ~90%, Sprint 1 ~90%, Sprint 2 ~60%, Sprint 3 ~25%.

---

## 5. Data facts you can rely on (7 Oct 2026)

- `contract_api.db`: 590,015 versions, 424,812 contracts (2020→now via `--history`, plus older still-running
  contracts via `--active`; earliest first_published 2000). 53,377 active. 3,217 item-16. 2.6 GB (≈1 GB raw JSON).
  28,894 contracts have no supplier ABN. `crawl_state` has `history:YYYY-MM = done` and (after incremental runs) `modified_through`.
- Website contract table `aus_contract.db`: 39,203 notices and growing (page fields), `son_url` on 5,169+.
  The old /Search route had missed ~48% of recent amendments (481 of 1,010 notices in 3 days).
- Some API notices have no page on the site (`/System/NotFound`, withdrawn): skipped, counted in
  `contract/page_not_found`.
- ATMs: 10,556 stored; 99 open on 7 Oct. Plans: 626 currently listed (1,433 rows stored incl. removed ones).
  BuyICT 6,792 (≈119 open). ACT 94 (0 contacts). NSW 284.
- Engine views: renewals within a year 14,858 (3,622 within 90 days); MMR primes 476 rows; IT candidates 331;
  BuyICT links 368 contracts for 207 requests; panel members 4,194; agency contacts 1,302; organisations 47,623
  (199 in review); actions 196; TODAY 15.
- NIAA list: 449 companies, 760 MMR contracts, as at 25 Feb 2026. 420 matched to an ABN; 38 companies have >1 ABN;
  12 "loose name" matches carry suggestion notes in `inputs/mmr/mmr_review.csv` (7 confirm, 5 reject) — Sirish decides.
- TODAY on 7 Oct (draft): ACT NOW — Health, APRA, ATO, Geoscience Australia (Exemption-16 agencies), DXC Technology
  (group) 86 (hook APRA CN4226826, 54 days), EY; INVESTIGATE — Fujitsu, Deloitte, KPMG, Defence, 2 plans;
  WATCH — 2 plans, 1 live BuyICT role (formal channel).

---

## 6. Google Sheets

- Folder: env `GSHEETS_FOLDER_ID` (Sirish's Drive folder; Sirish's Google account owns the sheets).
- Datasets and titles are in `publish_sheets.DATASETS`. "Illoura - Engine Views" (key `intelligence`) has tabs
  TODAY, renewals_180_days, mmr_primes, mmr_candidates_it, exemption_16, hot_list, open_ict, buyict_rates,
  agency_contacts. Published manually on 7 Oct 20:34; refreshed by every batch run with sync.
- There are 9 old duplicate spreadsheets (created 2 Oct) outside the folder, visible to the service account;
  they are unused leftovers. Don't delete without asking.

---

## 7. Machine state when this was written

- Windows Task Scheduler: `AusTenderScrapers` (every 6 h: run_spiders + backup), `AusTenderBuyIctHourly`,
  `AusTenderOutreachAgent` — **all Disabled** (Sirish's "stop everything", 6 Oct). Re-enabling needs an elevated
  PowerShell (`Enable-ScheduledTask -TaskName ...`) and Sirish's OK. Never enable the outreach agent.
- **Running in the background** (started 18:25): `python -m scrapy crawl aus_contract -a scope=active_it`
  (log `logs/aus_contract_active_it.log`), fetching panel IDs for 12,944 running ICT-services/labour-hire
  contracts at ~47 pages/min; at 20:44 it had 6,540 done; expected finish ≈ 23:15. If interrupted, rerun the same
  command: it skips stored notices. After it finishes: `python build_intelligence.py`, `python export_views.py`,
  `python publish_sheets.py --only intelligence` (panel contract counts improve).
- Git: everything up to commit `a39adcd` ("Action tracker ...") is pushed to `github/main`.
  GitHub had a major git-operations outage on 7 Oct ~20:40; if a push returns HTTP 500, check githubstatus.com and retry.
- Repo visibility: **public** since 22 Oct 2026 (also public 7–8 Oct, private 8–22 Oct). Before going public the history was rewritten to drop old CSV/log exports holding agency contact details; the pre-rewrite history is kept only in Sirish's local backups. Full data is shared privately, never through GitHub; the public `public-data-*` releases have personal details removed (`deploy/package_snapshot.py --public`). Still never commit `inputs/`, `exports/`, `databases/`, `.env`, keys, contact exports or anything from Illoura's own data (bench, relationships, RAP research, scores). The sender address now comes only from `.env` (`OUTREACH_FROM_EMAIL`); an older personal address remains in commits from 6–7 Oct by Sirish's choice.

---

## 8. Open decisions / inputs needed from Sirish

1. Approve `config/*.yaml` (keep IT equipment codes 4320–4322? "IT equipment supply" excluded by the spec's
   exclude list — OK? weights/thresholds as spec? extra panels?).
2. Decide the 12 loose MMR matches (`inputs/mmr/mmr_review.csv`, then `python mmr_list.py --review inputs/mmr/mmr_review.csv`).
3. Tenders ACT export file (only legitimate route to ACT contacts) → load into `act_tenders` contact fields.
4. Illoura's bench (role, clearance, city, available from, day-rate band; no names/CVs) → delivery readiness.
5. Relationships (who knows whom, where, strength, who can introduce) → warm paths (engine 6).
6. RAP table (organisation, RAP type/dates, action no., page, commitment, owner role, verified date) → brief step 6.
7. Commercial assumptions (positions, margin/hour, hours, stage probabilities, founders' target) → engine 8.
8. Turn the schedule back on (for the "one full week" test), and whether to keep healthcare categories in NSW.

---

## 9. Unfinished work — continue here

### 9.1 Action tracker: sheet sync (IN PROGRESS — finish first)

**Done:** `action_tracker.py` and its tests (`tests/test_action_tracker.py`):
- `connect()` → `databases/actions.db` (`actions`, `action_history`).
- `action_id(type, subject)` stable id `A-xxxxxxxx`; `today_list` sets `subject` (organisation; plan URL;
  live item URL).
- `merge(conn, actions, today)` adds new, refreshes MACHINE fields, never touches HUMAN fields
  (`owner, status, stage, follow_up, outcome, win_loss_reason, notes`).
- `record_edits(conn, sheet_rows)` stores edits (validates status ∈ STATUSES, stage ∈ STAGES, follow-up dates
  ISO / dd/mm/yyyy / "15 Oct 2026"), logs every change in `action_history`.
- `apply_to_today(conn, picked, today)` drops done/declined/won/lost/no-fit and snoozed-until-later, and
  prepends FOLLOW UP / OVERDUE items (accepted/in progress/snoozed with follow_up ≤ today).
- `tracker_rows(conn)` → rows for the Tracker tab (`TRACKER_COLUMNS`), due first, closed last.
- `build_intelligence.main` already calls merge + apply_to_today; `today`/`actions` tables have fixed
  columns (`ACTION_COLUMNS` incl. status, stage, action_id).

**To do:**
1. `publish_sheets.py`:
   - `TRACKER_TAB = 'Tracker'`; `_read_tracker(book)` → `book.worksheet('Tracker').get_all_records(default_blank='')`
     (return [] if `gspread.WorksheetNotFound`).
   - In `publish_all`, for key `intelligence`, after `spreadsheet_for` and **immediately before** `build()`:
     `with closing(action_tracker.connect()) as c: action_tracker.record_edits(c, _read_tracker(book))`.
   - Add `'Tracker': pd.DataFrame(action_tracker.tracker_rows(conn))` to `_engine_views()` (read it *after*
     record_edits so edits are not overwritten; read-then-write window must stay seconds).
   - After `publish(book, tabs)`, add dropdown validation on the Tracker `status` and `stage` columns via
     `book.batch_update({'requests': [{'setDataValidation': {'range': {...sheetId, startRowIndex: 1, startColumnIndex: col, endColumnIndex: col+1}, 'rule': {'condition': {'type': 'ONE_OF_LIST', 'values': [{'userEnteredValue': v} for v in STATUSES]}, 'showCustomUi': True, 'strict': False}}}]})`.
   - Add `pull_tracker()` (open registry['intelligence'], record_edits) for use at the start of a run.
2. `run_spiders.py`: before `build_intelligence.main([])`, if `GSHEETS_FOLDER_ID` and not `--no-sync`:
   `try: publish_sheets.pull_tracker() except Exception as e: print(...)` (never fail the run on it).
3. `export_views.py`: add a Tracker tab.
4. Tests: `tests/test_publish_engine_views.py` style — mock gspread book with a Tracker worksheet returning
   records; assert record_edits called before build and Tracker tab written; dropdown request sent.
5. README: a short "Tracking actions" section (statuses, stages, how follow-ups return to TODAY).
6. Run `python publish_sheets.py --only intelligence`, open the sheet, edit a Tracker row (status=accepted,
   follow_up=yesterday), rerun build + publish, confirm it appears as OVERDUE on TODAY. Then revert the edit.

### 9.2 One-page prime briefs (NOT STARTED — do second)

Spec format (DXC worked example). Build `prime_briefs.py`, called at the end of `build_intelligence.main`
for the primes in `actions` with `action_type == 'Tier-2 offer to prime'` (top 10 by score):
- **Header:** group/company name, ABN(s), priority/score from the action, owner Lane.
- **Why:** group MMR count (+ NIAA list date); "RAP: not yet researched" until a RAP table exists; whether it
  already supplies contractors (count of labour-hire contracts, `mmr_primes`/`contract_counts`).
- **What we don't know (ask them):** which contracts carry MMRs; contract- or organisation-based targets; where
  they are furthest behind. (Always present: public data never says.)
- **Steps table 1–9** exactly as spec engine 3: 1 Load (NIAA list, count, date); 2 Match (ABNs, match review
  state); 3 Candidates (top 3 active ≥ $7.5m from `mmr_candidates`, value, buyer, end, page link, "candidate");
  4 Sector check (category family: ICT services/labour hire → "plausible", equipment → "check"); 5 Tier-2 signals
  (renewals ≤180 days with buyer/end/days from `renewals` filtered to the group's ABNs; panels the ABNs belong to from
  `panel_members`); 6 RAP facts ("pending: RAP table"); 7 People ("pending: engine 6"); 8 Capability ("pending:
  bench"); 9 Score (score_parts + assumptions from the action).
- **Offer:** "cleared BAs, data and cyber people in Canberra (to confirm against the bench)"; immediate hook =
  the action's hook renewal.
- **Illustrative maths (label "hypothetical"):** largest candidate × 4% = participation needed over its life;
  one contractor-year ≈ median `estimated_day_rate` for analysis roles in `buyict_links` × 220 business days
  (label "estimate").
- **TODAY action card:** priority, owner, due, contact safety, desired outcome ("intro meeting / supplier onboarding").
- Every number with its source link.
Outputs: table `prime_briefs` in intelligence.db (one row per prime, text columns per section); HTML files
`exports/briefs/<slug>.html` (simple CSS, printable on one page); a `prime_briefs` tab in the Engine Views sheet
(add to `_engine_views()`); tests in `tests/test_prime_briefs.py` (DXC fixture: group MMR 6, candidate CN4262189,
hook CN4226826, maths labelled hypothetical, no invented people).

### 9.3 Next, in order (after 9.1 and 9.2)
1. Engine 5 demand/competitor view: by agency × role family, counts over 30/90/180/365 days (BuyICT
   `rfq_published_date`), clearance mix (parse "Baseline", "NV1", "NV2" from BuyICT requirements/criteria), median and
   range of `estimated_day_rate`, top winners (suppliers by linked contracts), incumbents on expiring work.
2. Engine 4: agency propensity view (Indigenous ICT contracts last 24 months, value, recency, Exemption 16 count,
   upcoming renewals/plans) and set-aside flags ($80k–$200k ICT opportunities).
3. AI for unclear titles (`opportunities.needs_review = 1`, ~10 open): use the existing Groq client pattern in
   `outreach_agent.ai_write` (model env `OUTREACH_LLM_MODEL`, key in `.env`), store prompt version + reason; keep a
   small labelled test set in `tests/fixtures/` and measure accuracy. Never let AI override a rule decision.
4. Monthly accuracy check script (20 random rows per view → CSV for a person to tick against the live source),
   data dictionary (`docs/data_dictionary.md` from the table schemas), runbook for broken sources, monthly cost note.
5. Rebuild outreach as drafts-for-approval: generate drafts for SAFE_GENERAL actions into a "Drafts" tab; a person
   approves; only then a separate, explicit send step (still never automatic). Every email names the sender + opt-out.
6. Engine 6/8 and the bench/RAP/relationship loaders once Sirish provides the data (simple sheets → tables).

---

## 10. Gotchas learned the hard way

- `DatabaseAwareDupeFilter` (duplicate_filter.py) skips URLs already in a spider's DB unless the request meta has
  `refresh_existing: True`; spiders set it where re-fetching is intended.
- Scrapy spiders here implement `async def start()` that yields from `start_requests()` (Scrapy 2.13 style).
- Amendment notices (CN123-A1) have their own page guid (item id), not the parent's (award id).
- AusTender API: `contractPublished` filters by the *original* publish date and includes later amendments; the
  agency contact sits on the supplier party's `contactPoint`; SONs/ATMs/plans are not in the API (400).
- buy.nsw needs Playwright with stealth (existing code); the search stops early when pages repeat.
- `holidays.Australia(subdiv='ACT')` for business days.
- Windows console encoding: scripts reconfigure stdout to UTF-8; when printing ad hoc, do the same.
- LF→CRLF warnings on commit are harmless.
- `publish_sheets` cannot create spreadsheets in a My Drive folder (no quota for service accounts): a person makes a
  blank sheet; the code claims it (titles starting "Untitled" are claimed).
- Tests must not hit the network; use `AUSTENDER_DB_DIR` temp dirs and mocks (see existing tests for patterns).
- Commit messages: plain, explain why; end with the co-author line used in history if you keep that convention.
