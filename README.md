# 🇦🇺 Australian Government Procurement Scraper

A high-performance Scrapy project that automatically scrapes Australian Government procurement data and stores it in optimized SQLite databases.

Works on Windows, macOS, and Linux.

## 🎯 What This Does

Each spider automatically saves data to its own dedicated SQLite database:

- `aus_contract` → `databases/aus_contract.db` (Government Contracts)
- `aus_tender` → `databases/aus_tender.db` (Active Tenders/ATMs)
- `planned_procurement` → `databases/planned_procurement.db` (Planned Procurements)
- `standing_offer` → `databases/standing_offer.db` (Standing Offer Notices)
- `buy_ict` → `databases/buy_ict.db` (BuyICT opportunities)
- `act_tender` → `databases/act_tender.db` (ACT tenders)
- `nsw_tender` → `databases/nsw_tender.db` (NSW contract awards and standing offers)

## ⚙️ Setup and Installation

### 1) Install Python 3.11
- Windows: Download and install from https://www.python.org/downloads/ and tick "Add Python to PATH" during installation.
- Confirm in a new terminal:
  - PowerShell: `python --version` or `py --version`

### 2) Create and activate a virtual environment

Pick your OS/shell and run ONE of the following.

- Windows (PowerShell)
  ```powershell
  py -3 -m venv .venv
  .\.venv\Scripts\Activate.ps1
  # If activation is blocked, allow scripts for this session only:
  # Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
  ```

- Windows (Command Prompt)
  ```bat
  py -3 -m venv .venv
  .venv\Scripts\activate.bat
  ```

- macOS / Linux (bash/zsh)
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

### 3) Install dependencies

Use the Python from your virtual environment:
```bash
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m playwright install chromium
```

For the dependency versions used by the regression checks, install with
`python -m pip install -r requirements-tested.txt` instead. Use a dedicated
virtual environment to avoid conflicts with other Python projects.

If you see build errors on Windows, upgrade pip/wheel as above and try again.

### 4) Optional: Google Sheets auto-sync

Create a `.env` file (next to `scrapy.cfg`) and add one of these options:

- Option A: Reference a Service Account JSON file (recommended)
  ```dotenv
  GSHEETS_AUTO_SYNC=True
  GSHEETS_SPREADSHEET_ID=YOUR_SPREADSHEET_ID
  # Windows path example (quotes allowed; code strips them):
  GSHEETS_SERVICE_ACCOUNT_FILE="C:\\Users\\you\\keys\\service_account.json"
  ```

- Option B: Embed JSON content directly
  ```dotenv
  GSHEETS_AUTO_SYNC=True
  GSHEETS_SPREADSHEET_ID=YOUR_SPREADSHEET_ID
  GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT='{"type":"service_account", ... }'
  ```

Note: The project automatically reads `.env` and accepts either a file path or inline JSON content.

## 🚀 Quick Start

You can run spiders directly with Scrapy or use the batch runner.

### A) Run individual spiders

- Windows (PowerShell/CMD): prefer `python -m scrapy` (works even if `scrapy` isn’t on PATH)
  ```powershell
  python -m scrapy crawl aus_contract
  python -m scrapy crawl aus_tender
  python -m scrapy crawl planned_procurement
  python -m scrapy crawl standing_offer
  python -m scrapy crawl buy_ict
  python -m scrapy crawl act_tender
  ```

- macOS / Linux:
  ```bash
  scrapy crawl aus_contract
  scrapy crawl aus_tender
  scrapy crawl planned_procurement
  scrapy crawl standing_offer
  scrapy crawl buy_ict
  scrapy crawl act_tender
  ```

Logs per spider are written under `logs/` (e.g., `logs/aus_contract.log`).
The batch runner uses `nsw_tender_fetch.py` for NSW; run it directly with `python nsw_tender_fetch.py --pages 5`.
BuyICT crawls all reported result pages by default. For a small check, use
`python -m scrapy crawl buy_ict -a max_pages=2 -a page_size=5`.

### B) Run all spiders with the batch runner

The project includes a simple runner that executes spiders sequentially, summarises DB deltas, and points to log files.

```powershell
# Windows / macOS / Linux
python run_spiders.py
```

Useful flags:
- `--spiders` to select specific spiders (default runs all)
- `--retries` number of retries per spider on failure (default 0)
- `--timeout` per-spider timeout in seconds (default 0 = no timeout)
- `--fail-fast` stop batch on first failure or sync error
- `--no-sync` disable Google Sheets writes even if enabled in `.env`
- `--report logs/batch-report.json` save a machine-readable summary

Examples
```powershell
# Run just contracts and tenders, unlimited time
python run_spiders.py --spiders aus_contract aus_tender

# Run all spiders but cap each to 3 hours
python run_spiders.py --timeout 10800

# Stop on first failure
python run_spiders.py --fail-fast

# Run without publishing to Google Sheets and save a summary
python run_spiders.py --no-sync --report logs/batch-report.json
```

Note: By default there is no time limit (timeout=0). Previously this default was 2 hours.

Unknown spider names, negative timeouts and negative retries are rejected. The
runner exits with code 1 for failed or incomplete crawls, rejected database items,
and failed syncs; code 0 means the selected batch completed successfully.
NSW errors now propagate to the runner instead of reporting success after an empty
or failed request. A legitimate empty NSW result requires manual verification.

Database paths default to this project's `databases/` directory even when a script
is invoked from another directory. Set `AUSTENDER_DB_DIR` to use a separate database
directory. Relative values resolve against the project directory. The batch runner
also launches its children from the project directory so Scrapy can find its settings.

#### Incremental runs (what a scheduled run does)

Each run only downloads what is new or can still change:

| Source | Listing walked every run | Detail pages fetched |
|---|---|---|
| `buy_ict` | all public opportunities (~110 API pages) | new + open ones; a closed one once, after it closes |
| `aus_tender` | open, recently closed, archived in last 14 days | new + open ones; a closed ATM once (its `/ShowClosed/` copy is final) |
| `aus_contract` | published since the last *proven complete* date − 3 days, in 7-day windows; then the newest-first "Last Updated" list until 3 pages in a row bring nothing new (max 60) | new CNs only (they never change) |
| `standing_offer` | all current SONs | new ones, and stored ones older than 7 days |
| `planned_procurement`, `act_tender`, `nsw_tender` | first pages | listing rows (small) |

- Every save is reported as `new`, `updated` (content really changed) or
  `unchanged`; `updated_at` only moves on a real change.
- AusTender search stops at 10,000 results, so searches are split into date
  windows, and each window's "of N records" total is checked against the
  notices actually listed. A shortfall fails the run (`crawl/coverage_failed`).
- BuyICT's `totalItems` includes opportunities only logged-in sellers can see
  (about a third). The public API never returns them, so coverage is checked
  per listing page instead.
- AusTender files a contract amendment (`CN123-A1`) under the *original* contract's
  publish date, so new amendments to older contracts never show up in a recent date
  window; the "Last Updated" pass catches them. For the same reason our per-month
  contract counts (amendment's own date) are higher than the site's.
- HTTP 429/503 responses slow the whole site down and are retried up to 8 times
  instead of being dropped.
- Backfill older notices: `python run_spiders.py --backfill-since 2015-01-01`.
- Each run writes `logs/runs/<time>.json`, keeps spider logs in `logs/history/`
  for 14 days, and never overlaps a still-running previous run.
- Set `HEALTHCHECK_URL` in `.env` to get an email when a run fails or stops arriving.

#### Releases feed (the product data)

Every run ends by updating `databases/releases.db` (`build_releases.py`): one
row per tender **released on or after the feed start date** (2026-10-02),
across AusTender ATMs, BuyICT, ACT, NSW open opportunities and newly added
planned procurements. Contract awards and standing offers are not releases and
stay out of it.

- `first_seen_at`: when we first picked the tender up; `updated_at`: last real change
  (close date extended, title edited, closed).
- New releases from each run are also written to `exports/releases/new_<time>.json/.csv`.
- `python build_releases.py` rebuilds it by hand; `--since YYYY-MM-DD` moves the start date.

#### Google Sheets (`publish_sheets.py`)

One spreadsheet per dataset in the `GSHEETS_FOLDER_ID` folder, refreshed after every run.
Sheets show what is useful now; the databases keep the full history.

| Sheet | Rows shown |
|---|---|
| New Releases | every release since the feed start |
| Tenders (ATM), ACT | published or closing in the last 12 months, plus everything open |
| BuyICT | open, plus published or closing in the last 12 months |
| NSW | open tenders; awards from the last 12 months; all standing-offer panels |
| Planned Procurements | those still listed on the site (seen by the latest crawl) |
| Contract Notices | published in the last 12 months |
| Standing Offers | offers whose period has not ended, with their supplier lists |

`SHEETS_KEEP_DAYS` in `.env` changes the 12-month window.

Data clean-up applied on save (verified against the live sites, 2026-10-04):
- Phone numbers that are only zeros or dashes (`0000000000`, `-----`) are stored blank:
  agencies type them to get past AusTender's required phone field.
- Planned procurements whose contact is "As for APP" get the agency plan's contact.
- BuyICT rich-text fields (requirements, industry briefing) are stored as plain text.
- Contract supplier state is filled from the postcode where the page lacked it;
  `scrapy crawl aus_contract -a refetch=missing_supplier` re-opens last-year notices
  whose supplier town is blank (resumable).
- AusTender ATM pages repeat the contact block; the phone is read once (older rows that
  stored it twice were repaired 2026-10-06).
- Not fixable from the data: many agencies leave the contract/standing-offer phone blank,
  many BuyICT buyers publish no contact (questions go through the portal), and ACT tender
  pages sit behind Cloudflare, so ACT rows carry listing fields only.

Every field a page shows is stored (2026-10-06):
- Named columns for the useful fields, e.g. contract notices: contact phone, branch,
  division, ATM ID, "Australian business engaged"; ATMs: multi-agency access, panel,
  multi-stage, other instructions, timeframe for delivery, address for lodgement; standing
  offers: contact phone; BuyICT: description, key duties, response requirements; NSW:
  contact phone, type, covered procurement, decision date, project goals, scope, files,
  requirements, assessment criteria, notices, amendments.
- `all_details` (JSON) keeps every labelled field on the page as well, so a field the site
  adds later is not lost. It is left out of the Google Sheets (its parts are columns).
- New schema columns are added to existing databases automatically on the next run.
- Records saved before this get the new fields when they are next re-read (open ATMs, open
  BuyICT and NSW opportunities, current standing offers: automatically). To fill older ones
  (resumable; about 47 pages a minute):
  `scrapy crawl aus_contract -a refetch=missing_details -a since=2025-10-06`
  (`aus_tender` and `standing_offer` take the same arguments; leave out `since` for all).

#### Contract notices from AusTender's API (`austender_api.py`)

AusTender's official open-data API (https://api.tenders.gov.au/ocds, Department of Finance,
CC BY 3.0 AU: credit AusTender) carries every contract notice and amendment. It covers
contracts only; tenders, standing offers and plans are still read from the website.

`databases/contract_api.db`:
- `contract_versions`: one row per published version (the notice and each amendment) exactly
  as received, with the raw JSON. A version already held is never overwritten.
- `contracts`: one row per contract, derived from its versions: original and current value,
  start, original and current end (Canberra dates), amendment and extension counts, UNSPSC
  code and family, Limited Tender Exemption and `is_indigenous_exemption` (Appendix A item 16),
  buyer, supplier and ABNs, agency contact (name, email, phone, branch, division).

Every batch run fetches notices created or amended since the last run (`contract_api` step).
One-off loads, resumable (a finished month is skipped when rerun):
`python austender_api.py --history 2020-01-01` (every contract published since then, about
four minutes per month) and `--active` (contracts still running, whenever published).
`--ids CN1,CN2` fetches named contracts (e.g. old contracts amended after they ended).

#### Which AusTender pages are read (robots.txt)

tenders.gov.au's robots.txt disallows `/Search/`, `/Reports/`, `/Cn/List` and `/Son/List`.
- **Contract notices:** the API lists them; `aus_contract` opens only each notice's own page
  (`/Cn/Show/<id>`, the id comes from the API record) for the fields the API leaves out: SON ID
  and its link, ATM ID, consultancy, confidentiality, suppliers invited, extension options.
  `-a scope=active_it` fills these in for running ICT and labour-hire contracts.
- **Open tenders:** the Current ATM list (`/Atm`); a stored open ATM that drops off the list is
  opened once more (its page redirects to the closed version). Withdrawn pages are remembered.
- **Panels:** found through the SON links on contract notices; current panels re-checked weekly.
- **Planned procurements:** the "List By Agency" tab (`/App/List`) links every agency's Annual
  Procurement Plan (`/App/Show/<plan>`), which lists all its planned procurements.

`ROBOTSTXT_OBEY` is on (settings.py), so a future change can't reach a disallowed page.

#### Engine views (`build_intelligence.py`, `today_list.py`, `config/`)

Rebuilt after every run into `databases/intelligence.db` (derived; source data never changes),
driven by the YAML files in `config/` (categories and keywords, panels, procurement rules,
scoring, organisation aliases, seed contacts):
`opportunities` (real status, ICT relevance with its reason, role family, contact safety),
`hot_list`, `renewals`, `exemption_use`, `buyict_links` (estimated day rates), `panel_members`,
`mmr_primes`, `mmr_candidates`, `nsw_small_contracts`, `organisations` (+ names, review queue),
`actions` and `today` (the TODAY list draft). `python export_views.py` writes them all to
`exports/illoura_views_<date>.xlsx` for review.

#### MMR list (`mmr_list.py`)

NIAA's "IPP companies with active MMR contracts" workbook gives only company names and contract
counts. Keep it in `inputs/mmr/` (git-ignored, Illoura confidential) and run:

```bash
python mmr_list.py --load inputs/mmr/<file>.xlsx --date YYYY-MM-DD   # stored as received, per list date
python mmr_list.py --match --export inputs/mmr/mmr_review.csv         # suggest supplier ABNs
python mmr_list.py --review inputs/mmr/mmr_review.csv                 # save confirmed / rejected
```

`databases/organisations.db`: `mmr_list` (the list, never overwritten) and `mmr_matches`
(company → supplier ABN from `contract_api.db`, with contract, ICT, labour-hire and active
counts, active contracts of $7.5m or more, and the review decision). Matching is by name;
`loose name` matches drop words like AUSTRALIA or SERVICES and need a person to check them.
Re-matching refreshes the counts and keeps every review decision.

#### Activity log (`activity_log.py`)

`databases/activity.db` is the audit trail of what was scraped and shared, and when:

| Table | One row per |
|---|---|
| `runs` | batch run: start, finish, status, machine |
| `scrapes` | spider per run: new / updated / unchanged counts, duration |
| `items` | record that was new, changed or closed (dataset, reference, title, link, time) |
| `shares` | Google Sheet refresh: time, sheet, rows per tab, status |
| `access` | person who can open a sheet: role, first seen, last seen, removed |
| `emails` | outreach email sent or failed: time, recipient, subject |

`python activity_log.py` prints a summary; `--items 50` lists the latest records.
Logging never stops a run; it is backed up and shipped to the VPS with the other databases.

#### Outreach agent (`outreach_agent.py`) — sending switched off

**Autonomous sending is off in code** (`SENDING_ALLOWED = False`): the Illoura spec allows no
autonomous emails (AI drafts, a person approves and sends) and no contact with a buyer while their
tender is live, and this agent's candidates are open-tender buyers. `--dry-run` still writes drafts.
It is to be rebuilt on the contact safety rules (spec section 8). What follows describes the
original design.

Runs every 15 minutes (Windows task `AusTenderOutreachAgent`; BuyICT is re-checked hourly in
Sydney business hours by `AusTenderBuyIctHourly`). Each run: reads its inbox (unsubscribe →
do-not-contact, bounce → invalid, real reply → forwarded to `OUTREACH_FORWARD_TO`), then — only
9am–4pm Sydney time on business days — emails new people-related releases. Each email is written
by the AI writer (Groq, `OUTREACH_LLM_MODEL`) from the tender's facts and
`templates/company_profile.yaml` only, checked (reference, no invented numbers, links or pricing
claims), and falls back to the template if a check fails. Signature and opt-out are added by code;
`templates/capability_statement.pdf` is attached.

- `OUTREACH_MODE=test` (default) sends everything to `OUTREACH_TEST_RECIPIENT`, subject
  `[TEST → real contact]`. `live` sends to the real contacts.
- Off switch: `SENDING` in the `settings` tab of the Outreach sheet. Log: its `sent` tab.
- Limits: 5/day for the first 14 days of sending in each mode (test sends do not warm up live),
  then `OUTREACH_DAILY_CAP`; 30s between emails during warm-up, 10s after; 14-day cooldown per
  address; pauses itself after 3 bounces in a day, and until tomorrow if Gmail refuses sends twice
  (e.g. its daily limit). Daily summary emailed after 4pm Sydney.
- Manual: `python outreach_agent.py run --dry-run` (preview), `run --now` (ignore the window),
  `inbox`, `summary`. Gmail login: a 16-letter App Password in `gaccount_pwd`.

#### Outreach emails (`outreach.py`)

Writes personalised emails to the contacts on new, people-related releases
(labour hire, staffing, recruitment, nursing and care) and sends them from your
mailbox after approval.

1. Fill in the `OUTREACH_*` and `SMTP_*` settings in `.env` (see `.env.example`).
2. `python outreach.py queue --from-db --dry-run` previews drafts; without `--dry-run`
   they are added to the `outreach` tab of the Google Sheet.
3. Type `yes` in the `approve` column of rows to send.
4. `python outreach.py send --dry-run` shows what would go out; `python outreach.py send` sends.
5. `python outreach.py suppress name@agency.gov.au` when someone opts out.

Built-in limits: one email per address every 14 days, 20 per run, 30 seconds apart;
contacts who ask for questions via the portal Q&A are never emailed; every email
names the sender and offers an opt-out. Edit the wording in `templates/outreach_email.txt`.

#### Scheduling every 6 hours

- VPS (Ubuntu 22.04/24.04), runs on its own:
  1. `python deploy/package_for_vps.py` builds `../aus_tender_vps.tar.gz` (code, `.env`,
     Google key and current databases, so the server doesn't re-download what is stored).
  2. `scp ../aus_tender_vps.tar.gz root@VPS_IP:/root/`
  3. `ssh root@VPS_IP "mkdir -p aus_tender && tar -xzf aus_tender_vps.tar.gz -C aus_tender && bash aus_tender/deploy/setup_vps.sh"`
     installs to `/opt/aus_tender` with a systemd timer (00:15/06:15/12:15/18:15 Sydney time,
     catches up after downtime) and daily database backups.
  4. `ssh root@VPS_IP "bash /opt/aus_tender/deploy/check_vps.sh --run"` does a first run and
     reports every source; afterwards `check_vps.sh` (no flag) shows the last run.
  Results: the `new_releases` tab of the Google Sheet (refreshed every run), and
  `/opt/aus_tender/databases/releases.db` + `exports/releases/` on the server.
  Run the schedule in one place only: two machines would keep separate databases.
- This Windows PC: `powershell -ExecutionPolicy Bypass -File deploy\register_windows_task.ps1`.

### C) Listings + contacts pipeline (Google Sheets)

`listings_pipeline.py` combines every database into one view and publishes it:

- **Listings**: every notice from all 7 sources, with source, sector, state(s),
  status (Open/Closed/Active/Planned…), dates, contact email and phone, and URL
- **Contacts**: one row per unique email (or phone, when there is no email). Each
  row has its primary sector, sources, states, live and total listing counts, and
  `inbox_type` (Personal vs Generic shared inbox). Phones use the +61 format.
- **Summary**: listing/contact counts by sector × source and sector × state

```powershell
python listings_pipeline.py                 # export + publish from existing DBs
python listings_pipeline.py --crawl         # crawl all portals first, then publish
python listings_pipeline.py --no-sheets     # CSVs only (exports/listings_master.csv, contacts_master.csv)
python listings_pipeline.py --share-with you@gmail.com --share-role writer
```

Set `GSHEETS_SHARE_WITH` (comma separated) in `.env` to share the spreadsheet
automatically after each run. Addresses that already have access are skipped.

## Verification

```powershell
python -m unittest discover -s tests -v
python -m scrapy list
```

The tests use temporary databases and a local HTTP server. They cover all seven
database schemas, updates, migration, validation, request handling, subprocess
timeouts, and a listing-to-detail crawl followed by a refresh. They do not contact
procurement portals or Google Sheets. The same tests are configured in GitHub
Actions for Windows and Linux.

Live portal availability and selector changes still require periodic smoke checks.
AusTender requests are throttled because previous runs received HTTP 429 responses.
Full crawls may take longer. Google Sheets credentials are optional; start from
`.env.example` for a local-only setup.

## 📊 Database Management

Query tooling is provided for quick inspection and exports.

```powershell
# List all databases
python db_query_tool.py --list

# Get database info and schema
python db_query_tool.py --info aus_contract

# Summary stats
python db_query_tool.py --stats aus_contract

# Custom SQL query
python db_query_tool.py --spider aus_contract --query "SELECT agency, COUNT(*) FROM contracts GROUP BY agency ORDER BY COUNT(*) DESC LIMIT 10"

# Export full table to CSV
python db_query_tool.py --spider aus_contract --export contracts_data.csv
```

## 🏗️ Architecture Features

### Smart pipelines
- Automatic routing: each spider writes to its own SQLite DB
- Data validation: ensures required fields are present
- Duplicate handling: DB UPSERTs + a dupe filter; mutable tenders and standing offers are refreshed on later runs
- Error handling: robust logging with per-spider stats

### Optimized databases
- Proper indexing on key fields (IDs, agency, dates, URL)
- WAL mode + tuned pragmas for reliability and performance
- JSON support for complex fields where needed

### Data quality
- Text cleaning to normalize whitespace and characters
- Timestamp tracking via `scraped_at`, `created_at`, `updated_at`

## 📁 Database Schemas (high-level)

- Contracts (`aus_contract.db`): contract IDs, agency, category, values, supplier info, key dates, contact info
- Tenders (`aus_tender.db`): ATM IDs, service/agency/category, publish/close, contacts, documents
- Planned Procurements (`planned_procurement.db`): title, agency, category, ETA to market, status, contacts
- Standing Offers (`standing_offer.db`): SON IDs, title/agency/category, period, participant agencies, suppliers

## 🔧 Configuration Pointers

- Pipelines in `settings.py` (actual classes):
  ```python
  ITEM_PIPELINES = {
      "pipelines.DataCleaningPipeline": 200,
      "pipelines.DatabasePipeline": 400,
  }
  ```
- Duplicate filter is enabled by default and checks the database: `duplicate_filter.DatabaseAwareDupeFilter`
- Databases are stored under `databases/`
- Logs are written under `logs/` (one file per spider)

## 📈 Example SQL

```sql
-- Top agencies by contract count
SELECT agency, COUNT(*) AS contract_count
FROM contracts
GROUP BY agency
ORDER BY contract_count DESC
LIMIT 10;

-- Recent high-value contracts
SELECT agency, supplier_name, contract_value_aud, publish_date
FROM contracts
WHERE contract_value_aud > 1000000
ORDER BY publish_date DESC;

-- Active tenders closing soon
SELECT service_name, agency, close_date_time
FROM tenders
WHERE close_date_time > datetime('now')
ORDER BY close_date_time ASC;
```

## 🪟 Windows Tips

- If `scrapy` isn’t recognized: use `python -m scrapy ...`
- If virtualenv activation is blocked in PowerShell: run `Set-ExecutionPolicy -Scope Process RemoteSigned`, then `.\.venv\Scripts\Activate.ps1`.
- File paths in `.env` can be quoted and use backslashes: `"C:\\path\\to\\file.json"` (quotes are automatically stripped).
- If you see build errors from pip, upgrade tooling first: `python -m pip install --upgrade pip setuptools wheel`.

## 🧪 Advanced

Run with different Scrapy settings on the fly:
```powershell
python -m scrapy crawl aus_contract -s LOG_LEVEL=INFO
python -m scrapy crawl aus_tender -s CONCURRENT_REQUESTS=32
```

Programmatic querying:
```python
from database_manager import DatabaseManager

db = DatabaseManager()
with db.get_connection('aus_contract') as conn:
    for row in conn.execute("SELECT * FROM contracts WHERE agency=?", ("Department of Defence",)):
        print(dict(row))
```

---

This setup gives you a robust, scalable solution that automatically manages data storage while providing powerful querying and export capabilities on Windows, macOS, and Linux.

## Audit fixes and data recovery

Contract values are stored as numbers, with source text retained in
`contract_value_raw`. Database initialization repairs legacy text amounts in a
transaction; unparseable amounts become NULL while their source text is preserved.
Supplier city/state labels now map to the correct database columns. Previously
lost locations require fetching the source again. To refresh saved contracts:

```powershell
$env:GSHEETS_AUTO_SYNC = 'False'
python -m scrapy crawl aus_contract -a refresh_existing=true
```

This is a full contract refresh and can take hours. Normal contract crawls still
skip saved notices unless the refresh argument is supplied.

Google Sheets append preserves existing dimensions and rows. Replacement exports
upload to temporary worksheets before publishing values in one atomic API batch;
standing-offer worksheets publish together. Target worksheet IDs and formatting
are preserved. Scraped strings are always written literally, including text
starting with `=` and values with leading zeros. Staging needs additional sheet
capacity; failure to allocate/upload staging leaves existing target data intact.

The batch runner now rejects ignored HTTP errors, exhausted retries, callback
exceptions, and explicit extraction failures. Empty/unrecognized listing pages
require manual verification, including a genuine zero-results page. BuyICT also
checks reported listing coverage and treats failed detail extraction as an error.
