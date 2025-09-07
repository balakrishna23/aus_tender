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
            
            if spider_name == 'standing_offer':
                # Normalize standing offers into multiple worksheets
                sheet_dfs = self._normalize_standing_offer(df)
                for title, sdf in sheet_dfs.items():
                    rows = len(sdf) + 1
                    cols = max(len(sdf.columns), 1)
                    ws = self._ensure_worksheet(spreadsheet, title, rows, cols)
                    ws.clear()
                    # Truncate any extreme long cells defensively
                    sdf = self._truncate_large_cells(sdf)
                    set_with_dataframe(ws, sdf, include_index=False)
                self.logger.info(
                    f"✅ Synced standing_offer into worksheets: {', '.join(sheet_dfs.keys())} (main rows: {len(sheet_dfs.get('standing_offers', pd.DataFrame()).index)})"
                )
                return True
            
            # Default behavior for other spiders: single worksheet
            worksheet_title = self.worksheet_map[spider_name]
            ws = self._ensure_worksheet(spreadsheet, worksheet_title, len(df) + 1, len(df.columns))
            ws.clear()
            df = self._truncate_large_cells(df)
            set_with_dataframe(ws, df, include_index=False)
            
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

    def _truncate_large_cells(self, df: pd.DataFrame, limit: int = 49000) -> pd.DataFrame:
        """Defensively truncate any string cell longer than `limit` to avoid API errors."""
        def truncate(val):
            if isinstance(val, str) and len(val) > limit:
                return val[:limit] + ' … [truncated]'
            return val
        if df.empty:
            return df
        for col in df.columns:
            if df[col].dtype == object:
                df[col] = df[col].map(truncate)
        return df

    def _normalize_standing_offer(self, df: pd.DataFrame) -> dict:
        """Split large JSON list fields into child worksheets and add counts to main."""
        import json as _json
        main_cols = [
            'title','son_id','agency','agency_reference_id','primary_category','description',
            'publish_date','standing_offer_period','procurement_method','atm_id',
            'multi_agency_access','multi_agency_access_type','panel_arrangement','panel_id','panel_url',
            'contact_name','email_address','office_postcode','url','scraped_at','created_at','updated_at'
        ]
        existing_main_cols = [c for c in main_cols if c in df.columns]
        main_df = df.copy()

        def parse_list(cell):
            if cell is None:
                return []
            if isinstance(cell, list):
                return cell
            if isinstance(cell, str) and cell.strip():
                try:
                    return _json.loads(cell)
                except Exception:
                    return []
            return []

        current_rows = []
        previous_rows = []
        participant_rows = []

        for _, row in main_df.iterrows():
            son_id = row.get('son_id')
            url = row.get('url')

            cur_list = parse_list(row.get('current_suppliers'))
            prev_list = parse_list(row.get('previous_suppliers'))
            part_list = parse_list(row.get('participant_agencies'))

            # counts
            main_df.at[_, 'current_suppliers_count'] = len(cur_list)
            main_df.at[_, 'previous_suppliers_count'] = len(prev_list)
            main_df.at[_, 'participant_agencies_count'] = len(part_list)

            # build child rows
            for item in cur_list:
                current_rows.append({
                    'son_id': son_id,
                    'supplier_name': item.get('Supplier Name') or item.get('supplier_name') or item.get('Name') or '',
                    'abn': item.get('ABN') or item.get('abn') or '',
                    'state': item.get('State') or item.get('state') or '',
                    'postcode': item.get('Postcode') or item.get('postcode') or '',
                    'deed_start_date': item.get('Deed Start Date') or item.get('deed_start_date') or '',
                    'deed_end_date': item.get('Deed End Date') or item.get('deed_end_date') or '',
                    'url': url,
                })
            for item in prev_list:
                previous_rows.append({
                    'son_id': son_id,
                    'supplier_name': item.get('Supplier Name') or item.get('supplier_name') or item.get('Name') or '',
                    'abn': item.get('ABN') or item.get('abn') or '',
                    'state': item.get('State') or item.get('state') or '',
                    'postcode': item.get('Postcode') or item.get('postcode') or '',
                    'deed_start_date': item.get('Deed Start Date') or item.get('deed_start_date') or '',
                    'deed_end_date': item.get('Deed End Date') or item.get('deed_end_date') or '',
                    'url': url,
                })
            for item in part_list:
                participant_rows.append({
                    'son_id': son_id,
                    'agency_name': item.get('Agency Name') or item.get('Supplier Name') or item.get('agency_name') or item.get('Name') or '',
                    'abn': item.get('ABN') or item.get('abn') or '',
                    'state': item.get('State') or item.get('state') or '',
                    'postcode': item.get('Postcode') or item.get('postcode') or '',
                    'deed_start_date': item.get('Deed Start Date') or item.get('deed_start_date') or '',
                    'deed_end_date': item.get('Deed End Date') or item.get('deed_end_date') or '',
                    'url': url,
                })

        # Drop the large JSON columns from main
        for col in ['current_suppliers','previous_suppliers','participant_agencies']:
            if col in main_df.columns:
                main_df = main_df.drop(columns=[col])

        # Reorder columns and add counts at the end if not present
        for count_col in ['current_suppliers_count','previous_suppliers_count','participant_agencies_count']:
            if count_col not in main_df.columns:
                main_df[count_col] = 0

        if existing_main_cols:
            ordered_cols = existing_main_cols + ['current_suppliers_count','previous_suppliers_count','participant_agencies_count']
            # Keep any other columns that exist but weren't listed
            tail_cols = [c for c in main_df.columns if c not in ordered_cols]
            main_df = main_df[ordered_cols + tail_cols]

        # Build child DataFrames
        cur_df = pd.DataFrame(current_rows, columns=['son_id','supplier_name','abn','state','postcode','deed_start_date','deed_end_date','url']) if current_rows else pd.DataFrame(columns=['son_id','supplier_name','abn','state','postcode','deed_start_date','deed_end_date','url'])
        prev_df = pd.DataFrame(previous_rows, columns=['son_id','supplier_name','abn','state','postcode','deed_start_date','deed_end_date','url']) if previous_rows else pd.DataFrame(columns=['son_id','supplier_name','abn','state','postcode','deed_start_date','deed_end_date','url'])
        part_df = pd.DataFrame(participant_rows, columns=['son_id','agency_name','abn','state','postcode','deed_start_date','deed_end_date','url']) if participant_rows else pd.DataFrame(columns=['son_id','agency_name','abn','state','postcode','deed_start_date','deed_end_date','url'])

        # Titles
        titles = {
            'standing_offers': main_df,
            'standing_offers_current_suppliers': cur_df,
            'standing_offers_previous_suppliers': prev_df,
            'standing_offers_participant_agencies': part_df,
        }
        return titles
