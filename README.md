# 🇦🇺 Australian Government Procurement Scraper

A high-performance Scrapy project that automatically scrapes Australian Government procurement data and stores it in optimized SQLite databases.

Works on Windows, macOS, and Linux.

## 🎯 What This Does

Each spider automatically saves data to its own dedicated SQLite database:

- `aus_contract` → `databases/aus_contract.db` (Government Contracts)
- `aus_tender` → `databases/aus_tender.db` (Active Tenders/ATMs)
- `planned_procurement` → `databases/planned_procurement.db` (Planned Procurements)
- `standing_offer` → `databases/standing_offer.db` (Standing Offer Notices)

## ⚙️ Setup and Installation

### 1) Install Python 3.8+
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
```

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
  ```

- macOS / Linux:
  ```bash
  scrapy crawl aus_contract
  scrapy crawl aus_tender
  scrapy crawl planned_procurement
  scrapy crawl standing_offer
  ```

Logs per spider are written under `logs/` (e.g., `logs/aus_contract.log`).

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

Examples
```powershell
# Run just contracts and tenders, unlimited time
python run_spiders.py --spiders aus_contract aus_tender

# Run all spiders but cap each to 3 hours
python run_spiders.py --timeout 10800

# Stop on first failure
python run_spiders.py --fail-fast
```

Note: By default there is no time limit (timeout=0). Previously this default was 2 hours.

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
- Duplicate handling: DB UPSERTs + a dupe filter that avoids re-scraping known URLs
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