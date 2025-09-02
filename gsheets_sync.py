"""
Google Sheets Auto-Sync Module
Automatically exports spider data to Google Sheets after each spider run.
"""

import logging
import os
import json
import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe
from pathlib import Path
from database_manager import DatabaseManager


class GoogleSheetsSync:
    """Handles automatic syncing of spider data to Google Sheets"""
    
    def __init__(self, spreadsheet_id: str, service_account_json_content: str):
        self.spreadsheet_id = spreadsheet_id
        self.service_account_json_content = service_account_json_content
        self.logger = logging.getLogger(__name__)
        self.db_manager = DatabaseManager()

        # Allow service_account_json_content to be either inline JSON or a file path
        if self.service_account_json_content and os.path.exists(self.service_account_json_content):
            with open(self.service_account_json_content, 'r', encoding='utf-8') as f:
                self.service_account_json_content = f.read()
        
        # Worksheet mapping: spider_name -> worksheet_title
        self.worksheet_map = {
            'aus_contract': 'contracts',
            'aus_tender': 'tenders',
            'planned_procurement': 'planned_procurements',
            'standing_offer': 'standing_offers'
        }
    
    def sync_spider(self, spider_name: str):
        """Sync a specific spider's data to its Google Sheet worksheet"""
        if spider_name not in self.worksheet_map:
            self.logger.warning(f"No worksheet mapping for spider: {spider_name}")
            return False
        
        try:
            self.logger.info(f"Starting Google Sheets sync for {spider_name}...")
            
            # Get data from database
            df = self._get_spider_data(spider_name)
            if df.empty:
                self.logger.info(f"No data found for {spider_name}, skipping sync")
                return True
            
            # Connect to Google Sheets
            credentials = json.loads(self.service_account_json_content)
            client = gspread.service_account_from_dict(credentials)
            spreadsheet = client.open_by_key(self.spreadsheet_id)
            
            # Get or create worksheet
            worksheet_title = self.worksheet_map[spider_name]
            worksheet = self._ensure_worksheet(spreadsheet, worksheet_title, len(df) + 1, len(df.columns))
            
            # Replace worksheet contents with fresh data
            worksheet.clear()
            set_with_dataframe(worksheet, df, include_index=False)
            
            self.logger.info(f"✅ Synced {len(df)} rows to worksheet '{worksheet_title}' for {spider_name}")
            return True
            
        except Exception as e:
            self.logger.error(f"❌ Failed to sync {spider_name} to Google Sheets: {e}")
            return False
    
    def _get_spider_data(self, spider_name: str) -> pd.DataFrame:
        """Get all data for a spider from its database"""
        if spider_name not in self.db_manager.SCHEMAS:
            return pd.DataFrame()
        
        table = self.db_manager.SCHEMAS[spider_name]["table_name"]
        with self.db_manager.get_connection(spider_name) as conn:
            # Preserve column order as in the table definition
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            query = f"SELECT {', '.join(cols)} FROM {table}"
            df = pd.read_sql_query(query, conn)
        
        return df
    
    def _ensure_worksheet(self, spreadsheet, title: str, rows: int, cols: int):
        """Get or create a worksheet with the given title"""
        try:
            worksheet = spreadsheet.worksheet(title)
        except gspread.exceptions.WorksheetNotFound:
            worksheet = spreadsheet.add_worksheet(
                title=title, 
                rows=str(max(rows, 2)), 
                cols=str(max(cols, 2))
            )
        else:
            # Resize to fit data (keep at least 2x2)
            try:
                worksheet.resize(rows=max(rows, 2), cols=max(cols, 2))
            except Exception:
                pass
        
        return worksheet