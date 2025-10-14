"""
Database Manager for Australian Government Procurement Data
Handles SQLite database operations with optimized performance and reliability.
"""

import sqlite3
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import contextmanager


class DatabaseManager:
    """Manages SQLite databases for different spider types"""
    
    # Database schemas for each spider type
    SCHEMAS = {
        'aus_contract': {
            'table_name': 'contracts',
            'schema': '''
                CREATE TABLE IF NOT EXISTS contracts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cn_id TEXT UNIQUE,
                    agency_reference_id TEXT,
                    professional_services TEXT,
                    agency TEXT,
                    category TEXT,
                    description TEXT,
                    publish_date TEXT,
                    execution_date TEXT,
                    max_end_date TEXT,
                    contract_period TEXT,
                    extension_options TEXT,
                    contract_value_aud REAL,
                    procurement_method TEXT,
                    son_id TEXT,
                    suppliers_invited TEXT,
                    confidentiality_contract TEXT,
                    confidentiality_outputs TEXT,
                    confidentiality_reasons_outputs TEXT,
                    consultancy TEXT,
                    contact_name TEXT,
                    email_address TEXT,
                    office_postcode TEXT,
                    supplier_name TEXT,
                    supplier_town_city TEXT,
                    supplier_postcode TEXT,
                    supplier_state_territory TEXT,
                    supplier_country TEXT,
                    supplier_abn TEXT,
                    url TEXT UNIQUE,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_contracts_cn_id ON contracts(cn_id)',
                'CREATE INDEX IF NOT EXISTS idx_contracts_agency ON contracts(agency)',
                'CREATE INDEX IF NOT EXISTS idx_contracts_category ON contracts(category)',
                'CREATE INDEX IF NOT EXISTS idx_contracts_publish_date ON contracts(publish_date)',
                'CREATE INDEX IF NOT EXISTS idx_contracts_supplier_name ON contracts(supplier_name)',
                'CREATE INDEX IF NOT EXISTS idx_contracts_url ON contracts(url)'
            ]
        },
        
        'aus_tender': {
            'table_name': 'tenders',
            'schema': '''
                CREATE TABLE IF NOT EXISTS tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    atm_id TEXT UNIQUE,
                    service_name TEXT,
                    agency TEXT,
                    category TEXT,
                    atm_type TEXT,
                    description TEXT,
                    location TEXT,
                    publish_date TEXT,
                    close_date_time TEXT,
                    conditions_for_participation TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    addenda_link TEXT,
                    atm_documents_link TEXT,
                    url TEXT UNIQUE,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_tenders_atm_id ON tenders(atm_id)',
                'CREATE INDEX IF NOT EXISTS idx_tenders_agency ON tenders(agency)',
                'CREATE INDEX IF NOT EXISTS idx_tenders_category ON tenders(category)',
                'CREATE INDEX IF NOT EXISTS idx_tenders_close_date ON tenders(close_date_time)',
                'CREATE INDEX IF NOT EXISTS idx_tenders_publish_date ON tenders(publish_date)',
                'CREATE INDEX IF NOT EXISTS idx_tenders_url ON tenders(url)'
            ]
        },
        
        'planned_procurement': {
            'table_name': 'planned_procurements',
            'schema': '''
                CREATE TABLE IF NOT EXISTS planned_procurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    agency TEXT,
                    agency_reference TEXT,
                    category TEXT,
                    estimated_date_of_approach_to_market TEXT,
                    last_updated TEXT,
                    status TEXT,
                    change_comments TEXT,
                    multi_agency_access TEXT,
                    multi_agency_access_type TEXT,
                    atms_link TEXT,
                    contact_name TEXT,
                    contact_phone TEXT,
                    contact_email TEXT,
                    url TEXT UNIQUE,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_planned_agency ON planned_procurements(agency)',
                'CREATE INDEX IF NOT EXISTS idx_planned_category ON planned_procurements(category)',
                'CREATE INDEX IF NOT EXISTS idx_planned_status ON planned_procurements(status)',
                'CREATE INDEX IF NOT EXISTS idx_planned_estimated_date ON planned_procurements(estimated_date_of_approach_to_market)',
                'CREATE INDEX IF NOT EXISTS idx_planned_procurements_url ON planned_procurements(url)'
            ]
        },
        
        'standing_offer': {
            'table_name': 'standing_offers',
            'schema': '''
                CREATE TABLE IF NOT EXISTS standing_offers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT,
                    son_id TEXT UNIQUE,
                    agency TEXT,
                    agency_reference_id TEXT,
                    primary_category TEXT,
                    description TEXT,
                    amendments TEXT,
                    publish_date TEXT,
                    standing_offer_period TEXT,
                    procurement_method TEXT,
                    atm_id TEXT,
                    multi_agency_access TEXT,
                    multi_agency_access_type TEXT,
                    panel_arrangement TEXT,
                    panel_id TEXT,
                    panel_url TEXT,
                    contact_name TEXT,
                    email_address TEXT,
                    office_postcode TEXT,
                    current_suppliers TEXT,  -- JSON field
                    previous_suppliers TEXT, -- JSON field
                    participant_agencies TEXT, -- JSON field
                    url TEXT UNIQUE,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_standing_son_id ON standing_offers(son_id)',
                'CREATE INDEX IF NOT EXISTS idx_standing_agency ON standing_offers(agency)',
                'CREATE INDEX IF NOT EXISTS idx_standing_category ON standing_offers(primary_category)',
                'CREATE INDEX IF NOT EXISTS idx_standing_publish_date ON standing_offers(publish_date)',
                'CREATE INDEX IF NOT EXISTS idx_standing_panel_id ON standing_offers(panel_id)',
                'CREATE INDEX IF NOT EXISTS idx_standing_panel_arrangement ON standing_offers(panel_arrangement)',
                'CREATE INDEX IF NOT EXISTS idx_standing_offers_url ON standing_offers(url)'
            ]
        },
        'buy_ict': {
            'table_name': 'buy_ict_tenders',
            'schema': '''
                CREATE TABLE IF NOT EXISTS buy_ict_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    rfq_type TEXT,
                    rfq_id TEXT,
                    rfq_published_date TEXT,
                    deadline_for_asking_questions TEXT,
                    rfq_closing_date TEXT,
                    buyer TEXT,
                    buyer_contact TEXT,
                    estimated_start_date TEXT,
                    initial_contract_duration TEXT,
                    extension_term TEXT,
                    number_of_extensions TEXT,
                    location_of_work TEXT,
                    working_arrangements TEXT,
                    industry_briefing TEXT,
                    requirements TEXT,
                    criteria TEXT,
                    url TEXT UNIQUE,
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_rfq_id ON buy_ict_tenders(rfq_id)',
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_buyer ON buy_ict_tenders(buyer)',
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_url ON buy_ict_tenders(url)'
            ]
        }
    }
    
    def __init__(self, db_directory: str = "databases"):
        """Initialize database manager"""
        self.db_directory = Path(db_directory)
        self.db_directory.mkdir(exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def __repr__(self):
        """Return a string representation of the DatabaseManager."""
        return f"<DatabaseManager(db_directory='{self.db_directory}')>"

    def get_db_path(self, spider_name: str) -> Path:
        """Get database file path for spider"""
        db_filename = f"{spider_name}.db"
        return self.db_directory / db_filename
    
    @contextmanager
    def get_connection(self, spider_name: str):
        """Get database connection with proper error handling"""
        db_path = self.get_db_path(spider_name)
        conn = None
        try:
            conn = sqlite3.connect(str(db_path), timeout=30.0)
            conn.row_factory = sqlite3.Row
            # Enable WAL mode for better concurrency
            conn.execute('PRAGMA journal_mode=WAL')
            conn.execute('PRAGMA synchronous=NORMAL')
            conn.execute('PRAGMA cache_size=10000')
            conn.execute('PRAGMA temp_store=MEMORY')
            yield conn
        except Exception as e:
            if conn:
                conn.rollback()
            self.logger.error(f"Database error for {spider_name}: {e}")
            raise
        finally:
            if conn:
                conn.close()
    
    def initialize_database(self, spider_name: str):
        """Initialize database schema for spider"""
        if spider_name not in self.SCHEMAS:
            raise ValueError(f"Unknown spider: {spider_name}")
        
        schema_info = self.SCHEMAS[spider_name]
        
        with self.get_connection(spider_name) as conn:
            # Create table
            conn.execute(schema_info['schema'])
            
            # Create indexes
            for index_sql in schema_info['indexes']:
                conn.execute(index_sql)
            
            conn.commit()
            self.logger.info(f"Initialized database for {spider_name}")
    
    def url_exists(self, spider_name: str, url: str) -> bool:
        """Check if a URL already exists in the database for a given spider."""
        if spider_name not in self.SCHEMAS:
            return False

        table_name = self.SCHEMAS[spider_name]['table_name']
        
        try:
            with self.get_connection(spider_name) as conn:
                cursor = conn.execute(f"SELECT 1 FROM {table_name} WHERE url = ? LIMIT 1", (url,))
                return cursor.fetchone() is not None
        except Exception as e:
            self.logger.error(f"Error checking URL existence for {spider_name}: {e}")
            return False

    def insert_item(self, spider_name: str, item: Dict[str, Any]) -> bool:
        """Insert or update item in database"""
        if spider_name not in self.SCHEMAS:
            self.logger.error(f"Unknown spider: {spider_name}")
            return False
        
        schema_info = self.SCHEMAS[spider_name]
        table_name = schema_info['table_name']
        
        # Prepare item data
        item_data = self._prepare_item_data(item, spider_name)
        
        try:
            with self.get_connection(spider_name) as conn:
                # Get table columns
                cursor = conn.execute(f"PRAGMA table_info({table_name})")
                columns = [row[1] for row in cursor.fetchall() 
                          if row[1] not in ['id', 'created_at', 'updated_at']]
                
                # Filter item data to match table columns
                filtered_data = {k: v for k, v in item_data.items() if k in columns}
                
                if not filtered_data:
                    self.logger.warning(f"No valid data to insert for {spider_name}")
                    return False
                
                # Prepare SQL for upsert
                columns_str = ', '.join(filtered_data.keys())
                placeholders = ', '.join([f':{key}' for key in filtered_data.keys()])
                
                update_str = ', '.join([f"{key}=excluded.{key}" for key in filtered_data.keys()])

                insert_sql = f"""
                    INSERT INTO {table_name} ({columns_str})
                    VALUES ({placeholders})
                    ON CONFLICT(url) DO UPDATE SET
                    {update_str},
                    updated_at = CURRENT_TIMESTAMP
                """
                
                conn.execute(insert_sql, filtered_data)
                conn.commit()
                
                self.logger.debug(f"Inserted/updated item in {spider_name} database")
                return True
                
        except Exception as e:
            self.logger.error(f"Failed to insert item for {spider_name}: {e}")
            return False
    
    def _prepare_item_data(self, item: Dict[str, Any], spider_name: str) -> Dict[str, Any]:
        """Prepare item data for database insertion"""
        prepared = {}
        
        for key, value in item.items():
            # Convert field names to snake_case for database
            db_key = self._convert_to_snake_case(key)
            
            # Handle special data types
            if isinstance(value, (list, dict)):
                # Convert complex data to JSON
                prepared[db_key] = json.dumps(value) if value else None
            elif value == "":
                # Convert empty strings to None
                prepared[db_key] = None
            else:
                prepared[db_key] = value
        
        # Add scraped timestamp
        prepared['scraped_at'] = datetime.now().isoformat()
        self.logger.info(f"Prepared data: {prepared}")
        return prepared
    
    def _convert_to_snake_case(self, name: str) -> str:
        """Convert a string to snake_case."""
        if not isinstance(name, str):
            return ""
        if name == "ATM's":
            return "atms_link"
        s = name.lower()
        s = re.sub(r'[\s\-&]+', '_', s)
        return re.sub(r'[^a-z0-9_]', '', s)

    def get_stats(self, spider_name: str) -> Dict[str, Any]:
        """Get database statistics"""
        if spider_name not in self.SCHEMAS:
            return {}
        
        table_name = self.SCHEMAS[spider_name]['table_name']
        
        try:
            with self.get_connection(spider_name) as conn:
                # Get row count
                cursor = conn.execute(f"SELECT COUNT(*) FROM {table_name}")
                total_rows = cursor.fetchone()[0]
                
                # Get latest entry
                cursor = conn.execute(f"SELECT MAX(created_at) FROM {table_name}")
                latest_entry = cursor.fetchone()[0]
                
                return {
                    'total_records': total_rows,
                    'latest_entry': latest_entry,
                    'database_file': str(self.get_db_path(spider_name))
                }
        except Exception as e:
            self.logger.error(f"Failed to get stats for {spider_name}: {e}")
            return {}
