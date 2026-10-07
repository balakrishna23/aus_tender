"""
Database Manager for Australian Government Procurement Data
Handles SQLite database operations with optimized performance and reliability.
"""

import sqlite3
import hashlib
import json
import logging
import re
import math
from decimal import Decimal, InvalidOperation
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
from contextlib import contextmanager
from project_paths import database_directory


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
                    contract_value_raw TEXT,
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
                    contact_phone TEXT,
                    branch TEXT,
                    division TEXT,
                    atm_id TEXT,
                    australian_business_engaged TEXT,
                    son_url TEXT,
                    all_details TEXT,  -- JSON: every labelled field on the page
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
                    multi_agency_access TEXT,
                    panel_arrangement TEXT,
                    multi_stage TEXT,
                    other_instructions TEXT,
                    timeframe_for_delivery TEXT,
                    address_for_lodgement TEXT,
                    all_details TEXT,  -- JSON: every labelled field on the page
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
                    contact_phone TEXT,
                    all_details TEXT,  -- JSON: every labelled field on the page
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
                    title TEXT,
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
                    sys_id TEXT,
                    status TEXT,
                    description TEXT,
                    key_duties TEXT,
                    response_requirements TEXT,  -- JSON list
                    main_details TEXT,  -- JSON: every field in the opportunity's main details
                    all_details TEXT,   -- JSON: everything else the opportunity API returns
                    scraped_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_rfq_id ON buy_ict_tenders(rfq_id)',
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_buyer ON buy_ict_tenders(buyer)',
                'CREATE INDEX IF NOT EXISTS idx_buy_ict_url ON buy_ict_tenders(url)',
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_buy_ict_sys_id ON buy_ict_tenders(sys_id)',
            ]
        },

        # AusTender's official contract API (austender_api.py): one row per published version
        # (original notice or amendment), exactly as received; never overwritten.
        'contract_api': {
            'table_name': 'contract_versions',
            'schema': '''
                CREATE TABLE IF NOT EXISTS contract_versions (
                    release_id TEXT PRIMARY KEY,
                    ocid TEXT,
                    cn_id TEXT,
                    notice_id TEXT,
                    amendment_no INTEGER,
                    tag TEXT,
                    release_date TEXT,
                    published_on TEXT,
                    date_signed TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    value REAL,
                    currency TEXT,
                    description TEXT,
                    agency_reference TEXT,
                    unspsc TEXT,
                    procurement_method TEXT,
                    limited_tender_exempt TEXT,
                    exemption_code TEXT,
                    exemption TEXT,
                    limited_tender_reason_code TEXT,
                    limited_tender_reason TEXT,
                    buyer TEXT,
                    buyer_abn TEXT,
                    supplier TEXT,
                    supplier_abn TEXT,
                    supplier_street TEXT,
                    supplier_locality TEXT,
                    supplier_region TEXT,
                    supplier_postcode TEXT,
                    supplier_country TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    contact_branch TEXT,
                    contact_division TEXT,
                    source_url TEXT,
                    page_url TEXT,
                    raw_json TEXT,
                    retrieved_at TEXT
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_versions_cn ON contract_versions(cn_id, amendment_no)',
                'CREATE INDEX IF NOT EXISTS idx_versions_published ON contract_versions(published_on)',
                'CREATE INDEX IF NOT EXISTS idx_versions_end ON contract_versions(end_date)',
            ]
        },

        'act_tender': {
            'table_name': 'act_tenders',
            'schema': '''
                CREATE TABLE IF NOT EXISTS act_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portal TEXT DEFAULT 'act_tender',
                    reference TEXT,
                    title TEXT,
                    agency TEXT,
                    category TEXT,
                    status TEXT,
                    close_date TEXT,
                    publish_date TEXT,
                    description TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    illoura_relevant INTEGER DEFAULT 0,
                    url TEXT UNIQUE,
                    scraped_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_act_title ON act_tenders(title)',
                'CREATE INDEX IF NOT EXISTS idx_act_agency ON act_tenders(agency)',
                'CREATE INDEX IF NOT EXISTS idx_act_category ON act_tenders(category)',
                'CREATE INDEX IF NOT EXISTS idx_act_close_date ON act_tenders(close_date)',
                'CREATE INDEX IF NOT EXISTS idx_act_relevant ON act_tenders(illoura_relevant)',
                'CREATE INDEX IF NOT EXISTS idx_act_url ON act_tenders(url)',
            ]
        },

        'nsw_tender': {
            'table_name': 'nsw_tenders',
            'schema': '''
                CREATE TABLE IF NOT EXISTS nsw_tenders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    portal TEXT DEFAULT 'nsw_tender',
                    reference TEXT,
                    title TEXT,
                    agency TEXT,
                    category TEXT,
                    status TEXT,
                    close_date TEXT,
                    publish_date TEXT,
                    value TEXT,
                    description TEXT,
                    contact_name TEXT,
                    contact_email TEXT,
                    contact_phone TEXT,
                    notice_type TEXT,
                    location TEXT,
                    procurement_type TEXT,
                    covered_procurement TEXT,
                    multi_agency_use TEXT,
                    related_opportunities TEXT,
                    timeframe_for_delivery TEXT,
                    estimated_decision_date TEXT,
                    project_goals TEXT,
                    scope TEXT,
                    related_files TEXT,
                    requirements TEXT,
                    assessment_criteria TEXT,
                    notices TEXT,
                    amendments TEXT,
                    all_details TEXT,  -- JSON: every field on the opportunity page
                    illoura_relevant INTEGER DEFAULT 0,
                    url TEXT UNIQUE,
                    scraped_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''',
            'indexes': [
                'CREATE INDEX IF NOT EXISTS idx_nsw_title ON nsw_tenders(title)',
                'CREATE INDEX IF NOT EXISTS idx_nsw_agency ON nsw_tenders(agency)',
                'CREATE INDEX IF NOT EXISTS idx_nsw_category ON nsw_tenders(category)',
                'CREATE INDEX IF NOT EXISTS idx_nsw_close_date ON nsw_tenders(close_date)',
                'CREATE INDEX IF NOT EXISTS idx_nsw_relevant ON nsw_tenders(illoura_relevant)',
                'CREATE INDEX IF NOT EXISTS idx_nsw_url ON nsw_tenders(url)',
            ]
        },
    }

    def __init__(self, db_directory: Optional[str] = None):
        """Initialize database manager"""
        self.db_directory = Path(db_directory) if db_directory is not None else database_directory()
        self.db_directory.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    def __repr__(self):
        """Return a string representation of the DatabaseManager."""
        return f"<DatabaseManager(db_directory='{self.db_directory}')>"

    def get_db_path(self, spider_name: str) -> Path:
        """Get database file path for spider"""
        if spider_name not in self.SCHEMAS:
            raise ValueError(f"Unknown spider: {spider_name}")
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

            # Columns added to SCHEMAS after a database was created are added in place.
            table_name = schema_info['table_name']
            have = {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})')}
            for column, kind in self._schema_columns(schema_info['schema']):
                if column not in have:
                    conn.execute(f'ALTER TABLE {table_name} ADD COLUMN {column} {kind}')

            if spider_name == 'aus_contract':
                columns = {row[1] for row in conn.execute('PRAGMA table_info(contracts)')}
                if 'contract_value_raw' not in columns:
                    conn.execute('ALTER TABLE contracts ADD COLUMN contract_value_raw TEXT')
                # Preserve source text while repairing legacy currency strings.
                for row in conn.execute("SELECT id, contract_value_aud FROM contracts "
                                        "WHERE typeof(contract_value_aud) = 'text'").fetchall():
                    conn.execute('UPDATE contracts SET contract_value_raw = COALESCE(contract_value_raw, ?), '
                                 'contract_value_aud = ? WHERE id = ?',
                                 (row[1], self._parse_contract_value(row[1]), row[0]))

            # Existing BuyICT databases were created before the spider captured titles.
            if spider_name == 'buy_ict':
                columns = {row[1] for row in conn.execute('PRAGMA table_info(buy_ict_tenders)')}
                for column in ('title', 'sys_id', 'status'):
                    if column not in columns:
                        conn.execute(f'ALTER TABLE buy_ict_tenders ADD COLUMN {column} TEXT')
                self._merge_buy_ict_duplicates(conn)

            conn.execute('CREATE TABLE IF NOT EXISTS crawl_state '
                         '(key TEXT PRIMARY KEY, value TEXT, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)')

            # Change detection: updated_at only moves when the stored content changes.
            table_name = schema_info['table_name']
            columns = {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})')}
            if 'content_hash' not in columns:
                conn.execute(f'ALTER TABLE {table_name} ADD COLUMN content_hash TEXT')

            # Create indexes
            for index_sql in schema_info['indexes']:
                conn.execute(index_sql)

            conn.commit()
            self.logger.info(f"Initialized database for {spider_name}")

    @staticmethod
    def _schema_columns(schema):
        """(name, type) of the plain columns in a CREATE TABLE statement (keys and defaults
        cannot be added by ALTER TABLE, so only simple typed columns are returned)."""
        columns = []
        for line in schema.splitlines():
            match = re.match(r'\s*([a-z_][a-z0-9_]*)\s+(TEXT|INTEGER|REAL|TIMESTAMP)\b(.*)', line, re.I)
            if match and not re.search(r'PRIMARY|UNIQUE|DEFAULT', match.group(3), re.I):
                columns.append((match.group(1), match.group(2).upper()))
        return columns

    BUY_ICT_URL = 'https://www.buyict.gov.au/public?id=opportunity_details&table={table}&sys_id={sys_id}&entry=opp_page'

    @classmethod
    def buy_ict_url(cls, url: str) -> Optional[str]:
        """Canonical BuyICT detail URL; legacy /sp?id= and /public?id= links share one form."""
        table = re.search(r'[?&]table=([^&]+)', url or '')
        sys_id = re.search(r'[?&]sys_id=([0-9a-f]{32})', url or '')
        if not (table and sys_id):
            return None
        return cls.BUY_ICT_URL.format(table=table.group(1), sys_id=sys_id.group(1))

    def _merge_buy_ict_duplicates(self, conn):
        """One row per BuyICT opportunity (sys_id); merges rows saved under legacy URL forms."""
        rows = conn.execute('SELECT * FROM buy_ict_tenders WHERE sys_id IS NULL').fetchall()
        for row in rows:
            canonical = self.buy_ict_url(row['url'])
            if canonical:
                conn.execute('UPDATE buy_ict_tenders SET sys_id = ? WHERE id = ?',
                             (re.search(r'sys_id=([0-9a-f]{32})', canonical).group(1), row['id']))
        duplicates = conn.execute('SELECT sys_id FROM buy_ict_tenders WHERE sys_id IS NOT NULL '
                                  'GROUP BY sys_id HAVING COUNT(*) > 1').fetchall()
        columns = [row[1] for row in conn.execute('PRAGMA table_info(buy_ict_tenders)')
                   if row[1] not in ('id', 'url', 'sys_id')]
        for (sys_id,) in duplicates:
            # Newest scrape wins; older copies only fill gaps it left empty.
            group = conn.execute('SELECT * FROM buy_ict_tenders WHERE sys_id = ? '
                                 'ORDER BY scraped_at DESC, id DESC', (sys_id,)).fetchall()
            keeper = dict(group[0])
            for other in group[1:]:
                for column in columns:
                    if keeper[column] in (None, '') and other[column] not in (None, ''):
                        keeper[column] = other[column]
                if other['created_at'] and (not keeper['created_at'] or other['created_at'] < keeper['created_at']):
                    keeper['created_at'] = other['created_at']
            conn.execute("DELETE FROM buy_ict_tenders WHERE sys_id = ? AND id != ?", (sys_id, keeper['id']))
            conn.execute(f"UPDATE buy_ict_tenders SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",
                         [keeper[c] for c in columns] + [keeper['id']])
        if duplicates:
            self.logger.info('Merged duplicate BuyICT rows for %s opportunities', len(duplicates))
        for row in conn.execute('SELECT id, url FROM buy_ict_tenders').fetchall():
            canonical = self.buy_ict_url(row['url'])
            if canonical and canonical != row['url']:
                conn.execute('UPDATE buy_ict_tenders SET url = ? WHERE id = ?', (canonical, row['id']))

    def get_state(self, spider_name: str, key: str) -> Optional[str]:
        with self.get_connection(spider_name) as conn:
            row = conn.execute('SELECT value FROM crawl_state WHERE key = ?', (key,)).fetchone()
            return row[0] if row else None

    def set_state(self, spider_name: str, key: str, value: str):
        with self.get_connection(spider_name) as conn:
            conn.execute('INSERT INTO crawl_state (key, value) VALUES (?, ?) ON CONFLICT(key) '
                         'DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP', (key, value))
            conn.commit()

    def load_column_map(self, spider_name: str, key_column: str, value_column: str) -> Dict[str, Any]:
        """Return {key: value} for every stored row, e.g. stored URL per notice."""
        table_name = self.SCHEMAS[spider_name]['table_name']
        with self.get_connection(spider_name) as conn:
            exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                                  (table_name,)).fetchone()
            if not exists:
                return {}
            return {row[0]: row[1] for row in conn.execute(
                f'SELECT {key_column}, {value_column} FROM {table_name} WHERE {key_column} IS NOT NULL')}
    
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

    def update_buy_ict_listing(self, updates: List[tuple]) -> int:
        """Apply (title, status, sys_id) from the listing to saved rows whose values differ."""
        if not updates:
            return 0
        with self.get_connection('buy_ict') as conn:
            cursor = conn.executemany(
                'UPDATE buy_ict_tenders SET title = COALESCE(?1, title), status = ?2, '
                'updated_at = CURRENT_TIMESTAMP WHERE sys_id = ?3 '
                'AND (status IS NOT ?2 OR (?1 IS NOT NULL AND title IS NOT ?1))',
                updates,
            )
            conn.commit()
            return cursor.rowcount

    IDENTITY_COLUMNS = {
        'aus_contract': 'cn_id',
        'aus_tender': 'atm_id',
        'standing_offer': 'son_id',
        'buy_ict': 'sys_id',
    }

    @staticmethod
    def _content_hash(data: Dict[str, Any]) -> str:
        stable = {k: v for k, v in data.items() if k not in ('scraped_at', 'content_hash')}
        return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()

    def insert_item(self, spider_name: str, item: Dict[str, Any]):
        """Insert or update an item. Returns 'new', 'updated' or 'unchanged' (False on failure)."""
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
                if 'content_hash' in columns:
                    filtered_data['content_hash'] = self._content_hash(filtered_data)

                identity_column = self.IDENTITY_COLUMNS.get(spider_name)
                lookup = [('url', filtered_data.get('url'))]
                if identity_column and filtered_data.get(identity_column):
                    lookup.insert(0, (identity_column, filtered_data[identity_column]))
                existing = None
                for column, value in lookup:
                    if value and column in columns:
                        existing = conn.execute(
                            f"SELECT id, {'content_hash' if 'content_hash' in columns else 'NULL'} "
                            f"FROM {table_name} WHERE {column} = ?", (value,)).fetchone()
                        if existing:
                            break
                if existing and existing[1] is not None and existing[1] == filtered_data.get('content_hash'):
                    # Same content: only record that the notice was re-verified.
                    conn.execute(f"UPDATE {table_name} SET scraped_at = ? WHERE id = ?",
                                 (filtered_data.get('scraped_at'), existing[0]))
                    conn.commit()
                    return 'unchanged'

                # Prepare SQL for upsert
                columns_str = ', '.join(filtered_data.keys())
                placeholders = ', '.join([f':{key}' for key in filtered_data.keys()])

                update_str = ', '.join([f"{key}=excluded.{key}" for key in filtered_data.keys()])
                conflict_clauses = [
                    f"ON CONFLICT(url) DO UPDATE SET {update_str}, updated_at = CURRENT_TIMESTAMP"
                ]
                if identity_column and filtered_data.get(identity_column):
                    conflict_clauses.append(
                        f"ON CONFLICT({identity_column}) DO UPDATE SET "
                        f"{update_str}, updated_at = CURRENT_TIMESTAMP"
                    )

                insert_sql = f"""
                    INSERT INTO {table_name} ({columns_str})
                    VALUES ({placeholders})
                    {' '.join(conflict_clauses)}
                """

                conn.execute(insert_sql, filtered_data)
                conn.commit()

                self.logger.debug(f"Inserted/updated item in {spider_name} database")
                return 'updated' if existing else 'new'
                
        except Exception as e:
            self.logger.error(f"Failed to insert item for {spider_name}: {e}")
            return False
    
    def _prepare_item_data(self, item: Dict[str, Any], spider_name: str) -> Dict[str, Any]:
        """Prepare item data for database insertion"""
        prepared = {}
        
        for key, value in item.items():
            # Convert field names to snake_case for database
            db_key = self._convert_to_snake_case(key)

            if spider_name == 'aus_contract' and db_key == 'contract_value_aud':
                prepared[db_key] = self._parse_contract_value(value)
                prepared['contract_value_raw'] = None if value is None else str(value)
                continue
            
            if 'phone' in db_key and self.is_placeholder_phone(value):
                prepared[db_key] = None  # '0000000000', '-----': typed to get past a required field
                continue

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
        self.logger.debug("Prepared %s item: %s", spider_name, prepared.get('url'))
        return prepared

    @staticmethod
    def is_placeholder_phone(value):
        """True for filler numbers agencies type into AusTender's required phone field."""
        if not isinstance(value, str) or not value.strip():
            return False
        digits = re.sub(r'\D', '', value)
        return not digits.strip('0')

    @staticmethod
    def state_from_postcode(postcode):
        """Australian state/territory for a 4-digit postcode (Australia Post ranges), else None."""
        text = str(postcode or '').strip()
        if not re.fullmatch(r'\d{4}', text):
            return None
        code = int(text)
        for low, high, state in ((200, 299, 'ACT'), (800, 999, 'NT'), (1000, 2599, 'NSW'),
                                 (2600, 2618, 'ACT'), (2619, 2899, 'NSW'), (2900, 2920, 'ACT'),
                                 (2921, 2999, 'NSW'), (3000, 3999, 'VIC'), (4000, 4999, 'QLD'),
                                 (5000, 5999, 'SA'), (6000, 6999, 'WA'), (7000, 7999, 'TAS'),
                                 (8000, 8999, 'VIC'), (9000, 9999, 'QLD')):
            if low <= code <= high:
                return state
        return None

    @staticmethod
    def _parse_contract_value(value):
        """Parse AUD amounts; unknown values stay NULL, never misleading text/zero."""
        if value is None:
            return None
        text = str(value).strip().replace('\xa0', ' ')
        text = re.sub(r'^(?:AUD\s*\$?|A\$|\$)\s*', '', text, flags=re.I)
        negative = text.startswith('(') and text.endswith(')')
        if negative:
            text = text[1:-1].strip()
        if not re.fullmatch(r'[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?', text):
            return None
        try:
            amount = float(Decimal(text.replace(',', '')))
            return (-amount if negative else amount) if math.isfinite(amount) else None
        except (InvalidOperation, ValueError, OverflowError):
            return None
    
    def _convert_to_snake_case(self, name: str) -> str:
        """Convert a string to snake_case."""
        if not isinstance(name, str):
            return ""
        if name == "ATM's":
            return "atms_link"
        aliases = {
            'supplier town/city': 'supplier_town_city',
            'supplier state/territory': 'supplier_state_territory',
        }
        if name.lower() in aliases:
            return aliases[name.lower()]
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
