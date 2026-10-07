
import os
from dotenv import load_dotenv
from project_paths import PROJECT_DIR

# Load environment variables from .env file
load_dotenv(PROJECT_DIR / '.env')

# Scrapy settings for aus project
#
# For simplicity, this file contains only settings considered important or
# commonly used. You can find more settings consulting the documentation:
#
#     https://docs.scrapy.org/en/latest/topics/settings.html
#     https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#     https://docs.scrapy.org/en/latest/topics/spider-middleware.html

BOT_NAME = "aus"

SPIDER_MODULES = ["spiders"]
NEWSPIDER_MODULE = "spiders"


# Crawl responsibly by identifying yourself (and your website) on the user-agent
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0"

# Obey robots.txt rules. Every route now uses pages the sites allow (contract notices are listed
# by AusTender's API, tenders by /Atm, plans by /App/List, panels by their links); this keeps it so.
ROBOTSTXT_OBEY = True

# Concurrency and throttling settings
CONCURRENT_REQUESTS = 8
CONCURRENT_REQUESTS_PER_DOMAIN = 2
DOWNLOAD_DELAY = 1
DOWNLOAD_TIMEOUT = 60


# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
DEFAULT_REQUEST_HEADERS = {
        'Accept-Language': 'en-US,en;q=0.9',
}

# Scrapy's default UserAgentMiddleware; no user-agent rotation.
DOWNLOADER_MIDDLEWARES = {
    # Runs before RetryMiddleware (550) so 429s slow the crawl instead of dropping notices.
    "middlewares.RateLimitBackoffMiddleware": 560,
    # Keep Scrapy's default UserAgentMiddleware (do not disable)
    # If you later add a custom UA middleware, place it after default headers, e.g., priority 460
}


# Enable or disable spider middlewares
# See https://docs.scrapy.org/en/latest/topics/spider-middleware.html
SPIDER_MIDDLEWARES = {
    "middlewares.SpiderNameMiddleware": 543,
}

# Enable or disable downloader middlewares
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html
#DOWNLOADER_MIDDLEWARES = {
#    "aus.middlewares.AusDownloaderMiddleware": 543,
#}

# Enable or disable extensions
# See https://docs.scrapy.org/en/latest/topics/extensions.html
#EXTENSIONS = {
#    "scrapy.extensions.telnet.TelnetConsole": None,
#}

# Configure item pipelines
# See https://docs.scrapy.org/en/latest/topics/item-pipeline.html
ITEM_PIPELINES = {
    "pipelines.DataCleaningPipeline": 200,
    "pipelines.DatabasePipeline": 400,
}

# Enable and configure the AutoThrottle extension (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/autothrottle.html
AUTOTHROTTLE_ENABLED = True
AUTOTHROTTLE_START_DELAY = 2
AUTOTHROTTLE_MAX_DELAY = 120
AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# The initial download delay
#AUTOTHROTTLE_START_DELAY = 5
# The maximum download delay to be set in case of high latencies
#AUTOTHROTTLE_MAX_DELAY = 60
# The average number of requests Scrapy should be sending in parallel to
# each remote server
#AUTOTHROTTLE_TARGET_CONCURRENCY = 1.0
# Enable showing throttling stats for every response received:
#AUTOTHROTTLE_DEBUG = False

# Enable and configure HTTP caching (disabled by default)
# See https://docs.scrapy.org/en/latest/topics/downloader-middleware.html#httpcache-middleware-settings
#HTTPCACHE_ENABLED = True
#HTTPCACHE_EXPIRATION_SECS = 0
#HTTPCACHE_DIR = "httpcache"
#HTTPCACHE_IGNORE_HTTP_CODES = []
#HTTPCACHE_STORAGE = "scrapy.extensions.httpcache.FilesystemCacheStorage"

# Set settings whose default value is deprecated to a future-proof value
FEED_EXPORT_ENCODING = "utf-8"

# Custom duplicate filter that checks database URLs
DUPEFILTER_CLASS = 'duplicate_filter.DatabaseAwareDupeFilter'

# Enable duplicate filter debugging (optional)
DUPEFILTER_DEBUG = False

# Logging settings
LOG_ENABLED = True
LOG_LEVEL = 'DEBUG'

# Create a logs directory if it doesn't exist
LOGS_DIR = 'logs'
if not os.path.exists(LOGS_DIR):
    os.makedirs(LOGS_DIR)

# The log file will be set dynamically by the SpiderNameMiddleware
# to 'logs/{spider_name}.log', but we can set a default here.
LOG_FILE = os.path.join(LOGS_DIR, 'scrapy.log')
LOG_FILE_APPEND = False
LOG_STDOUT = False

# Google Sheets Auto-Sync Configuration
GSHEETS_AUTO_SYNC = os.environ.get('GSHEETS_AUTO_SYNC', 'False').lower() in ('true', '1', 't', 'yes', 'y')

# Helper to strip accidental quotes from .env values
_def_strip = lambda v: v.strip().strip('"').strip("'") if isinstance(v, str) else v

GSHEETS_SPREADSHEET_ID = _def_strip(os.environ.get('GSHEETS_SPREADSHEET_ID'))

# Resolve service account credentials from env only (no hardcoded files)
GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = os.environ.get('GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT')
GSHEETS_SERVICE_ACCOUNT_FILE = os.environ.get('GSHEETS_SERVICE_ACCOUNT_FILE')

# Case A: JSON content env var provided — if it points to a file, read it; else treat as inline JSON
def _project_path(value):
    """Relative key-file paths resolve beside this project, whatever the working directory."""
    return value if not value or os.path.isabs(value) or value.startswith('{') else str(PROJECT_DIR / value)

if GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT:
    candidate = _project_path(_def_strip(GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT))
    if candidate and os.path.exists(candidate):
        with open(candidate, 'r', encoding='utf-8') as f:
            GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = f.read()
    else:
        GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = candidate
# Case B: File path provided
elif GSHEETS_SERVICE_ACCOUNT_FILE:
    candidate = _project_path(_def_strip(GSHEETS_SERVICE_ACCOUNT_FILE))
    if candidate and os.path.exists(candidate):
        with open(candidate, 'r', encoding='utf-8') as f:
            GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = f.read()

TWISTED_REACTOR = "twisted.internet.asyncioreactor.AsyncioSelectorReactor"
