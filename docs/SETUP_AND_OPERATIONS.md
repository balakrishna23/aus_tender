# Setup and operations guide

How to set up, run and manage this project on a new Windows PC, the same way it runs on Sirish's PC.
For background, decisions and history, read [HANDOFF.md](HANDOFF.md). For every command and option,
see the [README](../README.md).

> **In short:** clone the repo → install Python 3.11 and the requirements → restore a data snapshot
> (the public one from GitHub Releases, or the full one Sirish shares privately) → add `.env` and the
> Google key → run `python run_spiders.py`.

---

## 1. What runs where (reference setup on Sirish's PC)

| Item | On Sirish's PC |
|---|---|
| Project folder | `C:\work\aus_tender`. Keep it **outside OneDrive**, because syncing live SQLite files corrupts them and would upload the secrets. |
| Python | 3.11 for Windows, system-wide install, no virtualenv. A virtualenv (below) is recommended for a new PC. |
| Code | GitHub `balakrishna23/aus_tender` (public), branch `main`, remote name **`github`**. Ignore any remote called `origin` and the branch `main-old-history`. |
| Data | SQLite files in `databases/`, about 3.5 GB. They're not in git; they come from a data snapshot (section 3). |
| Secrets | `.env` and the Google service-account key file in the project root. They're not in git and not in the snapshot. Get them from Sirish privately (section 4). |
| Google Sheets | 10 spreadsheets in the "Tender Feed" Drive folder owned by Sirish's Google account, written by the service account |
| Scheduling | 3 Windows Task Scheduler tasks are registered but **disabled**. Runs are started by hand (section 6). |
| Local AI assistant | Optional. Ollama with `qwen3:4b-instruct` and `all-minilm:22m` (section 8). |
| CI | GitHub Actions runs all tests on Ubuntu and Windows on every push (`.github/workflows/tests.yml`). |

## 2. Install (once per PC)

Prerequisites: [Python 3.11](https://www.python.org/downloads/) (tick "Add Python to PATH") and
[Git](https://git-scm.com/download/win). The repo is public, so cloning needs no account. To push changes
you need write access (ask Sirish to add your GitHub account as a collaborator).

```powershell
mkdir C:\work; cd C:\work
git clone https://github.com/balakrishna23/aus_tender.git
cd aus_tender
git remote rename origin github                   # the team's remote is called "github"

py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1                      # if blocked: Set-ExecutionPolicy -Scope Process RemoteSigned
python -m pip install --upgrade pip
python -m pip install -r requirements-tested.txt  # exact versions used on Sirish's PC and in CI
python -m playwright install chromium             # NSW and ACT pages need a browser

python -m unittest discover -s tests              # expect all OK (one "[pipelines] ERROR" line is a deliberate test)
python -m unittest discover -s local_assistant -p "test_*.py"
```

Activate the virtualenv (`.\.venv\Scripts\Activate.ps1`) in every new terminal before running anything.

## 3. Get the data (a snapshot zip)

There are two kinds of snapshot. Both restore the same way.

| Snapshot | Where | What's in it |
|---|---|---|
| **Public** (`aus_tender_public_data_YYYYMMDD.zip`) | GitHub Releases, tag `public-data-YYYY-MM-DD`; anyone can download it | Public-source databases only (AusTender contracts, notices, tenders, panels, plans, BuyICT, WA contracts, the NIAA MMR list) with all personal contact details removed. No engine views (rebuilt locally), supplier contacts, review decisions, NSW or ACT data. See `DATA_README.md` inside. |
| **Full** (`aus_tender_snapshot_YYYYMMDD.zip`) | From Sirish privately (e.g. a shared Drive file). **Never put it on GitHub.** | Every database (except `sheet_imports.db`), including agency and supplier contacts and Illoura's internal plans, plus the Sheets registry, reviewed inputs, exports, the assistant's search index, business documents and a `HANDOFF.md` |

Public snapshot (download the newest `public-data-*` zip from
https://github.com/balakrishna23/aus_tender/releases):

```powershell
$tag = (Invoke-RestMethod https://api.github.com/repos/balakrishna23/aus_tender/releases |
        Where-Object tag_name -like 'public-data-*' | Select-Object -First 1)
$asset = $tag.assets | Select-Object -First 1
Invoke-WebRequest $asset.browser_download_url -OutFile "..\$($asset.name)"
python deploy/restore_snapshot.py "..\$($asset.name)"
python build_intelligence.py                 # rebuild the engine views from the restored data
python db_query_tool.py --list
```

Full snapshot (team members): save the zip Sirish shares into `C:\work`, then
`python deploy/restore_snapshot.py ..\aus_tender_snapshot_YYYYMMDD.zip --force`
(`--force` replaces any public-snapshot databases already restored).

`restore_snapshot.py` copies only data. The code comes from git, so pull to get newer code. It never overwrites
a database you already have unless you add `--force`.

## 4. Secrets: `.env` and the Google key (from Sirish, privately)

Copy `.env.example` to `.env` and fill it in. Never commit or email these. Use a password manager or hand them over in person.

| Setting | Needed for | Notes |
|---|---|---|
| `GSHEETS_FOLDER_ID` | Publishing the 10 Google Sheets | The "Tender Feed" Drive folder ID |
| `GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT` or `GSHEETS_SERVICE_ACCOUNT_FILE` | Same | The service-account key. Its `client_email` must be **Editor** on the folder. |
| `GSHEETS_AUTO_SYNC` | Legacy single-sheet sync | Keep it `False` when `GSHEETS_FOLDER_ID` is set |
| `OUTREACH_*`, `gaccount_pwd`, `groq_api_key` | Outreach agent only | Sending is switched off in code (`SENDING_ALLOWED = False`). Not needed to run the scrapers. |
| `HEALTHCHECK_URL` | Optional failure alerts | A healthchecks.io ping URL. Not set on Sirish's PC. |
| `BRAVE_API_KEY` | Optional supplier website search | Not set yet |

Without `.env`, everything works except Sheets publishing and outreach. Use `--no-sync` to make that explicit.

**Google Sheets rule:** the service account can't create files because it has no Drive quota. To add a sheet,
a person creates a blank "Untitled spreadsheet" inside the folder, and `publish_sheets.py` claims and renames it.
`databases/sheets_registry.json` (restored from the snapshot) maps each dataset to its spreadsheet.
If publishing fails with 404 or a permission error, first check that the folder is still shared with the service account.

## 5. Run it

```powershell
python run_spiders.py                     # full refresh: all sources, rebuild engine views, publish Sheets
python run_spiders.py --no-sync           # the same without touching Google Sheets
python run_spiders.py --spiders buy_ict   # one source (names: contract_api aus_contract aus_tender
                                          #   planned_procurement standing_offer buy_ict act_tender nsw_tender)
python state_contracts.py                 # WA state contract awards (only new financial years download)
python state_contracts.py --import        # QLD files saved by hand into inputs/state_contracts/qld/
```

A full run is incremental: it takes about 50 minutes after a week's gap (19 Oct 2026: 13:00–13:51) and less
when it runs often. In order, it:

1. Fetches new and amended contracts from the AusTender API (`contract_api`).
2. Reads new contract notice pages, tenders, plans, panels, BuyICT, ACT and NSW.
3. Rebuilds the new-releases feed (`releases.db`) and the engine views (`intelligence.db`: opportunities, renewals,
   exemption-16, MMR primes, agency contacts, actions, TODAY, prime briefs).
4. Pulls edits from the Tracker sheet, then publishes the 10 spreadsheets.
5. Writes `logs/runs/<time>.json`, the per-source logs in `logs/`, and `dashboard/index.html`.

Exit code 0 means everything succeeded. Exit code 1 means a source failed, coverage came up short, or a publish failed;
the summary at the end says which.

Other everyday commands:

| Task | Command |
|---|---|
| Engine views to Excel | `python export_views.py` → `exports/illoura_views_<date>.xlsx` |
| Republish only the engine views | `python publish_sheets.py --only intelligence` |
| Print the spreadsheet links | `python publish_sheets.py --links` |
| What changed recently | `python activity_log.py --items 50` |
| Operations dashboard | `python dashboard.py --open` |
| Look at the data | `python db_query_tool.py --list`, `--stats aus_contract`, `--spider aus_contract --query "SELECT ..."` |
| Load a new NIAA MMR list | `python mmr_list.py --load inputs/mmr/<file>.xlsx --date YYYY-MM-DD`, then `--match` and `--review` (README) |
| Import reviewed supplier routes | `python local_assistant/route_review.py --import <reviewed>.xlsx` |
| Backfill old contract history | `python austender_api.py --history 2020-01-01` (resumable, about 4 min per month) |

## 6. Scheduling (optional)

Sirish runs it by hand at the moment. To run it automatically on a PC that stays on:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\register_windows_task.ps1   # registers the 3 tasks below
Disable-ScheduledTask -TaskName AusTenderOutreachAgent                      # keep outreach off
Get-ScheduledTask -TaskName AusTender* | Select TaskName, State
```

| Task | What it does |
|---|---|
| `AusTenderScrapers` | Every 6 hours: full run, then `backup_databases.py --keep 14` |
| `AusTenderBuyIctHourly` | Hourly during Sydney business hours: BuyICT only, no Sheets |
| `AusTenderOutreachAgent` | Every 15 minutes: outreach agent. **Leave this disabled**; sending is off in code anyway. |

Enable or disable with `Enable-ScheduledTask` / `Disable-ScheduledTask -TaskName <name>`. Remove all three with
`Unregister-ScheduledTask -TaskName AusTenderScrapers,AusTenderOutreachAgent,AusTenderBuyIctHourly -Confirm:$false`.
Only **one PC** should run the scheduled scrape and publish to the Sheets at a time.

## 7. Manage it

**Backups.** `python backup_databases.py --keep 30` copies every database into `backups/daily-YYYYMMDD/`, plus a weekly copy
of `contract_api.db`. It also copies the hand-reviewed `inputs/`. It's safe to run while a spider is writing.
Take one before any big change.

**Sharing data snapshots** (how the snapshots in section 3 are made):

```powershell
# Public: personal details removed, safe for GitHub Releases (needs `gh auth login` with write access)
python deploy/package_snapshot.py --public
gh release create public-data-YYYY-MM-DD ..\aus_tender_public_data_YYYYMMDD.zip -R balakrishna23/aus_tender `
  --title "Public data YYYY-MM-DD" --notes-file ..\notes.md

# Full, for team members only: share the file privately (Drive), never on GitHub
python deploy/package_snapshot.py --note <handoff note>.md --docs "<folder of business documents>"
```

Each release file must be under 2 GiB (the October 2026 public zip is about 300–400 MB). The packer refuses
to include `.env` or key files. Before publishing a public snapshot, spot-check it for names, emails and phone numbers.

**Code changes.** Run the tests before pushing: `python -m unittest discover -s tests`. Then `git push github main`. CI runs the same tests.

**Health checks after a run:**
- The run summary ends with `Batch complete.` and every source shows `OK`.
- `DATA QUALITY:` lines flag sources that returned unusually little.
- `logs/<source>.log` has the details. A failed detail page is retried automatically next run.
- AusTender's 10,000-result search cap and BuyICT's seller-only items are handled by coverage checks. A
  `crawl/coverage_failed` result means a source was only partly read; rerun it.

**Rules the code enforces; keep them:**
- robots.txt is obeyed (`ROBOTSTXT_OBEY` on). Don't add pages a site disallows, and don't bypass bot challenges (that's why QLD is manual).
- Contacts: no guessed emails and no LinkedIn scraping. Agency contacts are never supplier contacts.
  Contacts on open tenders are `FORMAL_CHANNEL_ONLY`.
- No email is sent without Sirish's approval.
- The full databases hold personal contact details and Illoura's internal plans. Keep the full snapshot,
  `databases/`, `inputs/` and `exports/` off GitHub and anywhere public; this repo is public.

## 8. Local AI assistant (optional)

This is a question-and-answer screen over the databases at `http://127.0.0.1:8765`. Full details are in
[local_assistant/README.md](../local_assistant/README.md).

1. Install [Ollama](https://ollama.com/download) for Windows. On Sirish's PC a portable copy lives in the OneDrive
   project folder (`local_assistant/runtime/`, 5.6 GB with models); it isn't shared.
2. Pull the models and create the planning profile:
   ```powershell
   ollama pull qwen3:4b-instruct
   ollama pull all-minilm:22m
   cd local_assistant; ollama create illoura-planner -f Modelfile
   ```
3. Start it with `python assistant.py` (from `local_assistant/`), then open `http://127.0.0.1:8765`.
   `start.ps1` does the same but expects the portable Ollama in `runtime\ollama\`.
4. After a data refresh, rebuild the search index: `python local_assistant/build_hybrid_index.py`.
   The index restored from the snapshot is `local_assistant/runtime/hybrid.db`.

## 9. Troubleshooting

| Symptom | Fix |
|---|---|
| `Another run is still in progress` | A previous run holds `logs/.run.lock`. Wait for it; the lock is released when the process ends. |
| Sheets publish 403/404 | Check that the Drive folder is shared with the service account as Editor, and that `GSHEETS_FOLDER_ID` is right |
| NSW or ACT fail with browser errors | `python -m playwright install chromium` |
| `ZoneInfo` / time zone errors on Windows | `python -m pip install tzdata` |
| HTTP 429/503 from AusTender | Retried automatically, up to 8 times; rerun later if it still fails |
| A database is locked | Don't open the `.db` files in another tool during a run, and keep the folder out of OneDrive or Dropbox |
| `database disk image is malformed` | Restore that file from `backups/` or the latest snapshot release |

## 10. Data sources at a glance

| Source | Database | Access | Licence |
|---|---|---|---|
| AusTender contracts (API) | `contract_api.db` | `api.tenders.gov.au/ocds`, no key | CC BY 3.0 AU, credit AusTender |
| AusTender notices, tenders, panels, plans | `aus_contract`, `aus_tender`, `standing_offer`, `planned_procurement` | Pages allowed by robots.txt | CC BY 3.0 AU |
| BuyICT | `buy_ict.db`; sellers in `company_contacts.db` | Public pages, no login | Company fields only (ToU personal-information clause) |
| NSW buy.nsw, Tenders ACT | `nsw_tender.db`, `act_tender.db` | Public listing pages | NSW CC BY 4.0 (restricts commercial reproduction) |
| WA, QLD contract awards | `state_contracts.db` | WA open-data CSVs; QLD by hand | CC BY 4.0 |
| Derived engine views | `intelligence.db`, `releases.db`, `actions.db` | Rebuilt every run | Internal |
