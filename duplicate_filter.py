"""
Database-Aware Duplicate Filter for Scrapy

Extends Scrapy's built-in RFPDupeFilter to check URLs against the database,
preventing re-scraping of already processed URLs across multiple spider runs.
This implementation is memory-efficient as it queries the database directly
instead of loading all URLs into memory.
"""

import logging
from scrapy.dupefilters import RFPDupeFilter
from scrapy import signals
from scrapy.utils.job import job_dir
from database_manager import DatabaseManager

class DatabaseAwareDupeFilter(RFPDupeFilter):
    """
    An enhanced duplicate filter that checks for requests in two stages:
    1. Scrapy's built-in request fingerprinting for the current run (in-memory).
    2. The project's database for all previous runs (disk-based check).
    """
    
    def __init__(self, path=None, debug=False):
        super().__init__(path, debug)
        # Ensure database manager is always available; DUPEFILTER_CLASS is instantiated via from_settings
        # so from_crawler may not be called.
        self.database_manager = DatabaseManager()
        self.stats = {}  # Per-spider statistics
        self.logger = logging.getLogger(__name__)

    @classmethod
    def from_settings(cls, settings):
        """Create filter instance from Scrapy settings."""
        debug = settings.getbool('DUPEFILTER_DEBUG')
        # The path parameter is essential for the base RFPDupeFilter
        jobdir = job_dir(settings)
        return cls(jobdir, debug)
    
    @classmethod
    def from_crawler(cls, crawler):
        """Create filter instance from the crawler."""
        instance = cls.from_settings(crawler.settings)
        crawler.signals.connect(instance.spider_opened, signal=signals.spider_opened)
        crawler.signals.connect(instance.spider_closed, signal=signals.spider_closed)
        return instance
    
    def open(self):
        """Initialize the duplicate filter."""
        super().open()
        # Database manager is instantiated here to respect Scrapy's lifecycle.
        self.database_manager = DatabaseManager()
        self.logger.info("DatabaseAwareDupeFilter initialized.")

    def spider_opened(self, spider):
        """Called when a spider is opened."""
        # Initialize stats for this spider
        self.stats[spider.name] = {
            'database_duplicates_filtered': 0,
            'scrapy_duplicates_filtered': 0,
            'total_requests_processed': 0,
        }
        spider.logger.info(f"DatabaseAwareDupeFilter opened for spider: {spider.name}")

    def request_seen(self, request):
        """
        Check if the request has been seen before.
        Returns True if it's a duplicate, False otherwise.
        """
        # The SpiderNameMiddleware should add this to the meta
        spider_name = request.meta.get('spider_name')
        if not spider_name:
            self.logger.warning(f"Spider name not found in request meta for {request.url}. Dupe check may be unreliable.")
            # Fallback or default behavior
            spider_name = 'unknown'

        if spider_name in self.stats:
            self.stats[spider_name]['total_requests_processed'] += 1

        # 1. Check against Scrapy's in-memory dupefilter for the current run
        if super().request_seen(request):
            if spider_name in self.stats:
                self.stats[spider_name]['scrapy_duplicates_filtered'] += 1
            self.logger.debug(f"Scrapy duplicate detected: {request.url}")
            return True

        # 2. Check against the database for all previous runs
        if self._is_database_duplicate(request, spider_name):
            if spider_name in self.stats:
                self.stats[spider_name]['database_duplicates_filtered'] += 1
            self.logger.debug(f"Database duplicate detected: {request.url}")
            return True

        # If the request is new, it will be processed
        return False

    def _is_database_duplicate(self, request, spider_name):
        """Check if the request's URL exists in the database."""
        # Skip checking for pages that list multiple tenders/contracts (listing pages)
        if self._should_skip_duplicate_check(request.url):
            return False
        
        # Query the database to see if the URL already exists
        return self.database_manager.url_exists(spider_name, request.url)

    def _should_skip_duplicate_check(self, url):
        """Determines if a URL should be exempt from duplicate checking (e.g., search results pages)."""
        skip_patterns = [
            '/Search/',
            '/AdvancedSearch',
            'CnAdvancedSearch',
            'AtmAdvancedSearch', 
            'PpAdvancedSearch',
            'SonAdvancedSearch',
            'page=',
            'Type=',
            '&page=',
            '?page='
        ]
        
        return any(pattern in url for pattern in skip_patterns)

    def spider_closed(self, spider):
        """Log statistics when the spider closes."""
        spider_name = spider.name
        stats = self.stats.get(spider_name, {})
        
        total_filtered = stats.get('database_duplicates_filtered', 0) + stats.get('scrapy_duplicates_filtered', 0)
        total_processed = stats.get('total_requests_processed', 0)
        
        if total_processed > 0:
            self.logger.info(f"""
            -=-
            DatabaseAwareDupeFilter Statistics for spider: {spider_name}
            - Total requests processed: {total_processed}
            - Scrapy duplicates (in-session): {stats.get('scrapy_duplicates_filtered', 0)}
            - Database duplicates (persistent): {stats.get('database_duplicates_filtered', 0)}
            - Total duplicates filtered: {total_filtered}
            -=-
            """)

    def close(self, reason):
        """Clean up when the filter is closed."""
        super().close(reason)
        self.logger.info("DatabaseAwareDupeFilter closed.")
