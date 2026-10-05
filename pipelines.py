# Define your item pipelines here
#
# Don't forget to add your pipeline to the ITEM_PIPELINES setting
# See: https://docs.scrapy.org/en/latest/topics/item-pipeline.html

import logging
from itemadapter import ItemAdapter
import activity_log
from database_manager import DatabaseManager
from gsheets_sync import GoogleSheetsSync
from scrapy.exceptions import DropItem


class DatabasePipeline:
    """
    Smart pipeline that automatically routes spider data to appropriate SQLite databases
    Each spider gets its own optimized database with proper indexing and error handling
    """
    
    def __init__(self):
        self.db_manager = DatabaseManager()
        self.logger = logging.getLogger(__name__)
        self.stats = {}
        self.events = []  # new/changed records, written to activity.db at the end
    
    def open_spider(self, spider):
        """Initialize database for the spider when it starts"""
        spider_name = spider.name
        self.logger.info(f"Initializing database for spider: {spider_name}")
        
        try:
            # Initialize database schema
            self.db_manager.initialize_database(spider_name)
            
            # Initialize stats tracking
            self.stats[spider_name] = {
                'items_processed': 0,
                'items_saved': 0,
                'items_failed': 0
            }
            
            self.logger.info(f"Database initialized successfully for {spider_name}")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize database for {spider_name}: {e}")
            raise
    
    def process_item(self, item, spider):
        """Process each scraped item and save to appropriate database"""
        spider_name = spider.name
        adapter = ItemAdapter(item)
        
        # Update stats
        self.stats[spider_name]['items_processed'] += 1
        
        try:
            # Validate item has required data
            if not self._validate_item(adapter, spider_name):
                self.stats[spider_name]['items_failed'] += 1
                self.logger.warning(f"Item validation failed for {spider_name}")
                spider.crawler.stats.inc_value('database/items_failed')
                raise DropItem(f"Invalid {spider_name} item")
            
            # Save to database
            success = self.db_manager.insert_item(spider_name, dict(adapter))
            
            if success:
                self.stats[spider_name]['items_saved'] += 1
                spider.crawler.stats.inc_value('database/items_saved')
                if isinstance(success, str):
                    spider.crawler.stats.inc_value(f'database/items_{success}')
                    if success in ('new', 'updated'):
                        self.events.append(activity_log.item_event(spider_name, success, dict(adapter)))
                self.logger.debug(f"Item saved successfully for {spider_name}")
            else:
                raise RuntimeError(f"Failed to save item for {spider_name}")
            
        except DropItem:
            raise
        except Exception as e:
            self.stats[spider_name]['items_failed'] += 1
            spider.crawler.stats.inc_value('database/items_failed')
            self.logger.error(f"Error processing item for {spider_name}: {e}")
            raise DropItem(f"Database save failed for {spider_name}") from e
        
        return item
    
    def close_spider(self, spider):
        """Log statistics when spider finishes and optionally sync to Google Sheets"""
        spider_name = spider.name
        stats = self.stats.get(spider_name, {})
        activity_log.record_items(self.events)
        self.events = []
        
        self.logger.info(f"""
        Spider {spider_name} completed:
        - Items processed: {stats.get('items_processed', 0)}
        - Items saved: {stats.get('items_saved', 0)}
        - Items failed: {stats.get('items_failed', 0)}
        """)
        
        # Get database statistics
        db_stats = self.db_manager.get_stats(spider_name)
        if db_stats:
            self.logger.info(f"""
            Database statistics for {spider_name}:
            - Total records: {db_stats.get('total_records', 0)}
            - Database file: {db_stats.get('database_file', 'Unknown')}
            - Latest entry: {db_stats.get('latest_entry', 'Unknown')}
            """)
        
        # Auto-sync to Google Sheets if enabled
        if spider.crawler.settings.getbool('GSHEETS_AUTO_SYNC', False):
            spreadsheet_id = spider.crawler.settings.get('GSHEETS_SPREADSHEET_ID')
            service_account_json_content = spider.crawler.settings.get('GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT')
            
            if spreadsheet_id and service_account_json_content:
                try:
                    sync = GoogleSheetsSync(spreadsheet_id, service_account_json_content)
                    if not sync.sync_spider(spider_name):
                        self.logger.error(f"Google Sheets auto-sync failed for {spider_name}: sync returned failure")
                except Exception as e:
                    self.logger.error(f"Google Sheets auto-sync failed for {spider_name}: {e}")
            else:
                self.logger.warning("Google Sheets auto-sync enabled but missing GSHEETS_SPREADSHEET_ID or GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT")
    
    def _validate_item(self, adapter, spider_name):
        """Validate item has minimum required fields"""
        validation_rules = {
            'aus_contract': ['CN ID', 'Agency'],
            'aus_tender': ['ATM ID', 'Agency'],
            'planned_procurement': ['Title', 'Agency'],
            'standing_offer': ['SON ID', 'Agency'],
            'buy_ict': ['rfq_id'],
            'act_tender': ['reference'],
            'nsw_tender': ['reference'],
        }
        
        normalized = self.db_manager._prepare_item_data(dict(adapter), spider_name)
        required_fields = validation_rules.get(spider_name, []) + ['url']
        
        for field in required_fields:
            if not normalized.get(self.db_manager._convert_to_snake_case(field)):
                self.logger.warning(f"Missing required field '{field}' for {spider_name}")
                return False
        
        return True


class DataCleaningPipeline:
    """
    Pipeline for cleaning and normalizing data before database storage
    """
    
    def process_item(self, item, spider):
        adapter = ItemAdapter(item)
        
        # Clean text fields
        for field_name, value in adapter.items():
            if isinstance(value, str):
                # Remove extra whitespace
                cleaned_value = ' '.join(value.split())
                # Remove common unwanted characters
                cleaned_value = cleaned_value.replace('\xa0', ' ').replace('\n', ' ').replace('\r', ' ')
                adapter[field_name] = cleaned_value.strip()
        
        return item



