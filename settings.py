import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

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
#USER_AGENT = "aus (+http://www.yourdomain.com)"

# Obey robots.txt rules
ROBOTSTXT_OBEY = False

# Concurrency and throttling settings
CONCURRENT_REQUESTS = 16
CONCURRENT_REQUESTS_PER_DOMAIN = 16


# Disable cookies (enabled by default)
#COOKIES_ENABLED = False

# Disable Telnet Console (enabled by default)
#TELNETCONSOLE_ENABLED = False

# Override the default request headers:
DEFAULT_REQUEST_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36',
    'Accept-Language': 'en-US,en;q=0.9',
}

# A list of real User-Agent strings to rotate per request
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_4 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.4 Mobile/15E148 Safari/604.1",
    "Mozilla/5.0 (Windows NT 6.1; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/109.0",
]

# Enable the custom User-Agent rotating downloader middleware
DOWNLOADER_MIDDLEWARES = {
    "middlewares.AusUserAgentMiddleware": 400,
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
LOG_FILE = 'scrapy.log'
LOG_FILE_APPEND = False
LOG_STDOUT = False

# Google Sheets Auto-Sync Configuration
GSHEETS_AUTO_SYNC = os.environ.get('GSHEETS_AUTO_SYNC', 'False').lower() in ('true', '1', 't')
GSHEETS_SPREADSHEET_ID = os.environ.get('GSHEETS_SPREADSHEET_ID')

# Prioritize GSHEETS_SERVICE_ACCOUNT_FILE for path to JSON, then GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT for direct content
GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = os.environ.get('GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT')
gsheets_service_account_file = os.environ.get('GSHEETS_SERVICE_ACCOUNT_FILE')

if gsheets_service_account_file and os.path.exists(gsheets_service_account_file):
    with open(gsheets_service_account_file) as f:
        GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = f.read()
elif not GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT and os.path.exists('./dazzling-tiger-470805-b5-a17f4a3ccf05.json'):
    with open('./dazzling-tiger-470805-b5-a17f4a3ccf05.json') as f:
        GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT = f.read()
