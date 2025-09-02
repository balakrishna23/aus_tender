#!/usr/bin/env python3
"""
Export spider data from SQLite databases to Google Sheets (one worksheet per spider).

Prereqs:
- Enable Google Sheets API in a Google Cloud project
- Create a Service Account and download its JSON key
- Share the target Spreadsheet with the service account email (Editor)

Install deps:
  pip install gspread gspread-dataframe pandas google-auth

Usage examples:
  # Export all spiders to a single spreadsheet (replace contents)
  python export_to_gsheets.py \
    --spreadsheet "https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit" \
    --service-account-json /path/to/service_account.json \
    --spiders aus_contract aus_tender planned_procurement standing_offer

  # Export only tenders (append to the existing sheet)
  python export_to_gsheets.py \
    --spreadsheet <ID_OR_URL> \
    --service-account-json /path/to/service_account.json \
    --spiders aus_tender \
    --append

Notes:
- Default behavior is replace the worksheet contents each run (safer and consistent)
- Worksheet names default to the table names; you can override with --worksheet-map
"""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import gspread
from gspread_dataframe import set_with_dataframe

from database_manager import DatabaseManager


SPIDER_DEFAULTS = [
    "aus_contract",
    "aus_tender",
    "planned_procurement",
    "standing_offer",
]


def parse_args():
    parser = argparse.ArgumentParser(description="Export spider data to Google Sheets")
    parser.add_argument(
        "--spreadsheet",
        required=True,
        help="Spreadsheet ID or full URL",
    )
    parser.add_argument(
        "--service-account-json",
        required=True,
        help="Path to Google Service Account JSON key",
    )
    parser.add_argument(
        "--spiders",
        nargs="*",
        default=SPIDER_DEFAULTS,
        help=f"Spiders to export (default: {' '.join(SPIDER_DEFAULTS)})",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append to worksheet instead of replacing contents",
    )
    parser.add_argument(
        "--worksheet-map",
        type=str,
        default=None,
        help=(
            "Optional JSON mapping of spider -> worksheet title. "
            "Example: '{\"aus_tender\":\"Tenders\",\"aus_contract\":\"Contracts\"}'"
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional limit on number of rows exported per spider",
    )
    return parser.parse_args()


essential_lib_msg = (
    "Missing dependency. Please install: pip install gspread gspread-dataframe pandas google-auth"
)


def open_spreadsheet(spreadsheet: str, sa_json_path: str):
    if not Path(sa_json_path).exists():
        raise SystemExit(f"Service Account JSON not found: {sa_json_path}")

    try:
        client = gspread.service_account(filename=sa_json_path)
    except Exception as e:
        raise SystemExit(f"Failed to load service account: {e}\n{essential_lib_msg}")

    try:
        if spreadsheet.startswith("http"):
            sh = client.open_by_url(spreadsheet)
        else:
            sh = client.open_by_key(spreadsheet)
    except Exception as e:
        raise SystemExit(f"Failed to open spreadsheet: {e}")
    return sh


def get_df_for_spider(db: DatabaseManager, spider: str, limit: Optional[int] = None) -> pd.DataFrame:
    if spider not in db.SCHEMAS:
        raise SystemExit(f"Unknown spider: {spider}")

    table = db.SCHEMAS[spider]["table_name"]
    with db.get_connection(spider) as conn:
        # Preserve column order as in the table definition
        cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
        query = f"SELECT {', '.join(cols)} FROM {table}"
        if limit:
            query += f" LIMIT {int(limit)}"
        df = pd.read_sql_query(query, conn)
    return df


def ensure_worksheet(spreadsheet, title: str, rows: int, cols: int):
    try:
        ws = spreadsheet.worksheet(title)
    except gspread.exceptions.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=str(max(rows, 2)), cols=str(max(cols, 2)))
    else:
        # Resize to fit data (keep at least 2x2)
        try:
            ws.resize(rows=max(rows, 2), cols=max(cols, 2))
        except Exception:
            pass
    return ws


def write_df(ws, df: pd.DataFrame, append: bool = False):
    if append:
        # Find the next empty row (1-indexed)
        current_values = ws.get_all_values()
        start_row = len(current_values) + 1 if current_values else 1
        set_with_dataframe(ws, df, row=start_row, include_column_header=not current_values)
    else:
        # Clear values (preserve formatting) then write
        ws.clear()
        set_with_dataframe(ws, df, include_index=False)


def main():
    args = parse_args()

    # Load optional worksheet map
    worksheet_map: Dict[str, str] = {}
    if args.worksheet_map:
        try:
            worksheet_map = json.loads(args.worksheet_map)
        except Exception as e:
            raise SystemExit(f"Invalid --worksheet-map JSON: {e}")

    db = DatabaseManager()
    sh = open_spreadsheet(args.spreadsheet, args.service_account_json)

    exported: List[str] = []
    for spider in args.spiders:
        print(f"Exporting {spider}...")
        df = get_df_for_spider(db, spider, limit=args.limit)
        ws_title = worksheet_map.get(spider, db.SCHEMAS[spider]["table_name"])  # default to table name
        ws = ensure_worksheet(sh, ws_title, rows=(len(df) + 1), cols=len(df.columns))
        write_df(ws, df, append=args.append)
        print(f"  - Rows: {len(df)} -> Worksheet: {ws_title}")
        exported.append(spider)

    print("\nDone. Exported:")
    for s in exported:
        print(f" - {s}")


if __name__ == "__main__":
    main()
