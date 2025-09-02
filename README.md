# 🇦🇺 Australian Government Procurement Scraper

A high-performance Scrapy project that automatically scrapes Australian Government procurement data and stores it in optimized SQLite databases.

## 🎯 **What This Does**

Each spider automatically saves data to its own dedicated SQLite database:

- **`aus_contract`** → `databases/aus_contract.db` (Government Contracts)
- **`aus_tender`** → `databases/aus_tender.db` (Active Tenders/ATMs)  
- **`planned_procurement`** → `databases/planned_procurement.db` (Planned Procurements)
- **`standing_offer`** → `databases/standing_offer.db` (Standing Offer Notices)

## ⚙️ Setup and Installation

To get this project running on a new machine, follow these steps:

### 1. Prerequisites

*   **Python 3.8+**: Ensure Python is installed. You can download it from [python.org](https://www.python.org/downloads/).

### 2. Clone the Repository

First, obtain the project code. If it's in a Git repository, use:

```bash
git clone <repository_url>
cd <project_directory>
```

Replace `<repository_url>` with the actual URL of your Git repository and `<project_directory>` with the name of the cloned directory.

### 3. Set Up a Virtual Environment

It's highly recommended to use a virtual environment to manage project dependencies and avoid conflicts with other Python projects.

```bash
python3 -m venv .venv
source .venv/bin/activate  # On Windows, use: .venv\Scripts\activate
```

### 4. Install Dependencies

With the virtual environment activated, install all required Python packages listed in `requirements.txt`:

```bash
pip install -r requirements.txt
```

### 5. Configure Google Sheets Integration (Optional)

If you plan to use the Google Sheets auto-sync feature, you'll need to configure the service account credentials.

1.  **Get Service Account JSON**: Obtain your Google Service Account JSON key file.
2.  **Create `.env` file**: In the root directory of the project (where `scrapy.cfg` is located), create a file named `.env`.
3.  **Add Configuration to `.env`**: 
    You have two options for providing the service account credentials:

    *   **Option A: Reference the JSON file path (Recommended)**
        If you have the JSON key file saved on the client's PC (e.g., `~/keys/my-service-account.json`), add this line to your `.env` file:
        ```
        GSHEETS_AUTO_SYNC=True
        GSHEETS_SPREADSHEET_ID=YOUR_SPREADSHEET_ID_HERE
        GSHEETS_SERVICE_ACCOUNT_FILE=/path/to/your/service_account.json
        ```
        Replace `/path/to/your/service_account.json` with the actual absolute path to the JSON file.

    *   **Option B: Embed the JSON content directly**
        You can also paste the entire JSON content directly into the `.env` file. **Be careful with special characters and quotes.**
        ```
        GSHEETS_AUTO_SYNC=True
        GSHEETS_SPREADSHEET_ID=YOUR_SPREADSHEET_ID_HERE
        GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT='{"type": "service_account", "project_id": "...", "private_key_id": "...", "private_key": "...", "client_email": "...", "client_id": "...", "auth_uri": "...", "token_uri": "...", "auth_provider_x509_cert_url": "...", "client_x509_cert_url": "...", "universe_domain": "..."}'
        ```
        Replace `YOUR_SPREADSHEET_ID_HERE` with the ID of your Google Sheet.

Remember to keep the virtual environment activated (`source .venv/bin/activate`) whenever you are working with the project.

## 🚀 **Quick Start**

### 1. Run Individual Spiders
```bash
# Scrape contracts (saves to databases/aus_contract.db)
scrapy crawl aus_contract

# Scrape tenders (saves to databases/aus_tender.db)  
scrapy crawl aus_tender

# Scrape planned procurements (saves to databases/planned_procurement.db)
scrapy crawl planned_procurement

# Scrape standing offers (saves to databases/standing_offer.db)
scrapy crawl standing_offer
```

### 2. Run All Spiders
```bash
# Run all spiders sequentially
scrapy crawl aus_contract && scrapy crawl aus_tender && scrapy crawl planned_procurement && scrapy crawl standing_offer
```

## 📊 **Database Management**

### View Database Information
```bash
# List all databases
python db_query_tool.py --list

# Get database info and schema
python db_query_tool.py --info aus_contract

# Get summary statistics
python db_query_tool.py --stats aus_contract
```

### Query Databases
```bash
# Custom SQL queries
python db_query_tool.py --spider aus_contract --query "SELECT agency, COUNT(*) FROM contracts GROUP BY agency LIMIT 10"

# Export to CSV
python db_query_tool.py --spider aus_contract --export contracts_data.csv
```

### Example Queries
```sql
-- Top agencies by contract count
SELECT agency, COUNT(*) as contract_count 
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

## 🏗️ **Architecture Features**

### ✅ **Smart Pipeline System**
- **Automatic Routing**: Each spider's data goes to its own database
- **Data Validation**: Ensures required fields are present
- **Duplicate Handling**: Prevents duplicate records with UPSERT operations
- **Error Recovery**: Robust error handling with detailed logging

### ✅ **Optimized Databases**
- **Proper Indexing**: Fast queries on key fields (agency, dates, IDs)
- **WAL Mode**: Better concurrency and performance
- **Data Types**: Optimized column types for each field
- **JSON Support**: Complex data (supplier lists) stored as JSON

### ✅ **Data Quality**
- **Text Cleaning**: Removes extra whitespace and unwanted characters
- **Field Validation**: Ensures data integrity
- **Timestamp Tracking**: Records when data was scraped
- **Schema Consistency**: Standardized field names across databases

## 📁 **Database Schemas**

### Contracts Database (`aus_contract.db`)
```sql
- cn_id (TEXT, UNIQUE)
- agency, category, description
- contract_value_aud (REAL)
- supplier information (name, location, ABN)
- dates (publish, execution, end)
- contact information
```

### Tenders Database (`aus_tender.db`)
```sql
- atm_id (TEXT, UNIQUE)
- service_name, agency, category
- dates (publish, close)
- location, conditions
- contact information
- document links
```

### Planned Procurement Database (`planned_procurement.db`)
```sql
- title, agency, category
- estimated_date_of_approach_to_market
- status, change_comments
- contact information
```

### Standing Offers Database (`standing_offer.db`)
```sql
- son_id (TEXT, UNIQUE)
- title, agency, primary_category
- standing_offer_period
- supplier panels (JSON)
- participant agencies (JSON)
```

## 🔧 **Configuration**

### Pipeline Settings (in `settings.py`)
```python
ITEM_PIPELINES = {
    "pipelines.DataCleaningPipeline": 200,    # Clean data
    "pipelines.DuplicatesPipeline": 300,      # Handle duplicates  
    "pipelines.DatabasePipeline": 400,        # Save to database
}
```

### Database Location
- Default: `databases/` directory
- Configurable in `database_manager.py`

## 📈 **Monitoring & Logging**

### Real-time Statistics
Each spider run shows:
- Items processed/saved/failed
- Database record counts
- Performance metrics

### Log Files
- `scrapy.log` - Detailed scraping logs
- Console output - Real-time progress

## 🛠️ **Advanced Usage**

### Custom Database Queries
```python
from database_manager import DatabaseManager

db_manager = DatabaseManager()
with db_manager.get_connection('aus_contract') as conn:
    cursor = conn.execute("SELECT * FROM contracts WHERE agency = ?", ("Department of Defence",))
    results = cursor.fetchall()
```

### Batch Processing
```bash
# Run all spiders with custom settings
scrapy crawl aus_contract -s LOG_LEVEL=INFO
scrapy crawl aus_tender -s CONCURRENT_REQUESTS=32
```

## 🚨 **Error Handling**

The system includes comprehensive error handling:
- **Database Connection Issues**: Automatic retry with exponential backoff
- **Data Validation Errors**: Logged with details for debugging
- **Network Issues**: Scrapy's built-in retry mechanisms
- **Schema Mismatches**: Graceful handling of unexpected data

## 📊 **Performance Optimizations**

- **Connection Pooling**: Efficient database connections
- **Batch Inserts**: Optimized for large datasets
- **Proper Indexing**: Fast queries on common fields
- **WAL Mode**: Better concurrency
- **Memory Management**: Efficient handling of large datasets

## 🎯 **Best Practices**

1. **Regular Runs**: Set up cron jobs for automated scraping
2. **Database Maintenance**: Periodic VACUUM operations for optimization
3. **Backup Strategy**: Regular database backups
4. **Monitoring**: Track scraping success rates and data quality
5. **Rate Limiting**: Respect website rate limits (configured in settings)

---

## 🏆 **Why This Approach is Optimal**

✅ **Production-Ready**: Enterprise-grade architecture with proper error handling  
✅ **Scalable**: Handles large datasets efficiently  
✅ **Maintainable**: Clean separation of concerns, easy to modify  
✅ **Reliable**: ACID transactions, duplicate prevention, data validation  
✅ **Fast**: Optimized queries, proper indexing, connection pooling  
✅ **Flexible**: Easy to add new spiders or modify existing ones  

This setup gives you a robust, scalable solution that automatically manages data storage while providing powerful querying capabilities. Each spider run is completely automated - just run the command and your data is safely stored in its dedicated database!