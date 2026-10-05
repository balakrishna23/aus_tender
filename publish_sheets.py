#!/usr/bin/env python3
"""One Google Spreadsheet per dataset, kept in a Shared Drive folder.

The service account cannot own Drive files (no storage quota), so spreadsheets are
created inside a Shared Drive (or a folder in one) where it is a Content manager.
Each spreadsheet is created once; its ID is remembered in
databases/sheets_registry.json and refreshed in place after every run, so links
never change.

Usage:
  python publish_sheets.py                       # create missing sheets, refresh all
  python publish_sheets.py --only releases buyict
  python publish_sheets.py --links               # print the link of every sheet
Settings: GSHEETS_FOLDER_ID in .env (the Shared Drive or folder ID from its URL).
"""
import argparse
import json
from datetime import date, timedelta
import logging
import os
import sqlite3
import sys
from contextlib import closing

import gspread
import pandas as pd
from dotenv import load_dotenv

import activity_log
from project_paths import PROJECT_DIR, database_directory
from sheets_writer import replace_worksheets

log = logging.getLogger('publish_sheets')

REGISTRY = 'sheets_registry.json'
HIDDEN_COLUMNS = {'content_hash', 'id'}
CELL_LIMIT = 49000  # Google Sheets rejects cells over 50,000 characters
# Sheets show what is useful now: open/active items plus the last year. The databases
# (and their backups) keep the full history.
KEEP_DAYS = 365


def _table(db, sql):
    path = database_directory() / f'{db}.db'
    if not path.exists():
        return pd.DataFrame()
    with closing(sqlite3.connect(path)) as conn:
        try:
            frame = pd.read_sql_query(sql, conn)
        except Exception as e:  # missing table on a fresh install
            log.warning('Skipping %s: %s', db, e)
            return pd.DataFrame()
    return frame.drop(columns=[c for c in frame.columns if c in HIDDEN_COLUMNS])


def _cutoff():
    return date.today() - timedelta(days=int(os.environ.get('SHEETS_KEEP_DAYS') or KEEP_DAYS))


def _recent(frame, *date_columns, keep=None):
    """Rows dated within KEEP_DAYS on any of `date_columns`, plus rows where keep(row) is true."""
    if frame.empty:
        return frame
    from build_releases import parse_day
    cutoff = _cutoff()

    def wanted(row):
        if keep is not None and keep(row):
            return True
        return any((day := parse_day(str(row.get(c) or ''))) is not None and day >= cutoff
                   for c in date_columns)
    return frame[frame.apply(wanted, axis=1)].reset_index(drop=True)


def _status_is(*values):
    values = {v.lower() for v in values}
    return lambda row: str(row.get('status') or '').strip().lower() in values


def _offer_active(row):
    """Standing offer period '1-Jul-2024 to 30-Jun-2027' has not ended."""
    from build_releases import parse_day
    end = parse_day(str(row.get('standing_offer_period') or '').split(' to ')[-1])
    return end is not None and end >= date.today()


def _still_listed(frame):
    """Planned procurements found on the site by the latest crawl. Every crawl re-stamps each
    listed one, so anything not re-seen within a day of the newest stamp has been removed.
    Measured from the newest stamp, not the clock, so a few missed runs never empty the sheet."""
    if frame.empty:
        return frame
    seen = pd.to_datetime(frame['scraped_at'], errors='coerce', format='mixed')
    return frame[seen >= seen.max() - timedelta(days=1)].reset_index(drop=True)


def _standing_offers():
    from gsheets_sync import GoogleSheetsSync
    frame = _table('standing_offer', 'SELECT * FROM standing_offers ORDER BY created_at DESC')
    frame = _recent(frame, keep=_offer_active)  # active offers only, with their supplier lists
    if frame.empty:
        return {'standing_offers': frame}
    # Same normalisation as the old combined sheet: one tab per supplier list.
    return GoogleSheetsSync._normalize_standing_offer(GoogleSheetsSync.__new__(GoogleSheetsSync), frame)


def _releases():
    frame = _table('releases', 'SELECT * FROM releases ORDER BY first_seen_at DESC, published_on DESC')
    lead = ['first_seen_at', 'status', 'source', 'published_on', 'closes_at', 'title', 'buyer',
            'contact_name', 'contact_email', 'url']
    return {'releases': frame[[c for c in lead if c in frame] + [c for c in frame if c not in lead]]}


# key -> (spreadsheet title, function returning {tab title: DataFrame}). Newest rows first.
DATASETS = {
    'releases': ('Tender Feed - New Releases', _releases),
    'tenders': ('AusTender - Tenders (ATM)', lambda: {
        'tenders': _recent(_table('aus_tender', 'SELECT * FROM tenders ORDER BY created_at DESC'),
                           'publish_date', 'close_date_time')}),
    'buyict': ('BuyICT - Opportunities', lambda: {
        'opportunities': _recent(_table('buy_ict', 'SELECT * FROM buy_ict_tenders ORDER BY created_at DESC'),
                                 'rfq_published_date', 'rfq_closing_date', keep=_status_is('open', ''))}),
    'nsw': ('NSW - buy.nsw', lambda: {
        'open_tenders': _table('nsw_tender', "SELECT * FROM nsw_tenders WHERE portal = 'nsw_opportunity' "
                                             "AND status = 'Open' ORDER BY created_at DESC"),
        'awards_and_panels': _recent(_table('nsw_tender', "SELECT * FROM nsw_tenders WHERE portal != "
                                                          "'nsw_opportunity' ORDER BY created_at DESC"),
                                     'publish_date', 'close_date', keep=_status_is('standing offer'))}),
    'act': ('ACT - Tenders ACT', lambda: {
        'tenders': _recent(_table('act_tender', 'SELECT * FROM act_tenders ORDER BY created_at DESC'),
                           'publish_date', 'close_date', keep=_status_is('open'))}),
    'planned': ('AusTender - Planned Procurements', lambda: {
        'planned_procurements': _still_listed(_table(
            'planned_procurement', 'SELECT * FROM planned_procurements ORDER BY created_at DESC'))}),
    'contracts': ('AusTender - Contract Notices', lambda: {
        'contracts': _recent(_table('aus_contract', 'SELECT * FROM contracts ORDER BY created_at DESC'),
                             'publish_date')}),
    'standing_offers': ('AusTender - Standing Offers (Panels)', _standing_offers),
    # Managed by outreach.py (drafts are edited by people); only created here, never overwritten.
    'outreach': ('Tender Feed - Outreach', None),
}


def registry_path():
    return database_directory() / REGISTRY


def load_registry():
    try:
        return json.loads(registry_path().read_text(encoding='utf-8'))
    except FileNotFoundError:
        return {}


def save_registry(registry):
    registry_path().write_text(json.dumps(registry, indent=2), encoding='utf-8')


class RetryingHTTPClient(gspread.http_client.HTTPClient):
    """Retries Google's temporary failures (rate limits, 5xx, dropped connections).

    Permanent errors (403 permission or quota, 404) are raised at once.
    """

    RETRY_STATUS = {408, 429, 500, 502, 503, 504}
    DELAYS = (5, 15, 45, 90)

    def request(self, *args, **kwargs):
        import time
        import requests
        for attempt, delay in enumerate(self.DELAYS + (None,)):
            try:
                return super().request(*args, **kwargs)
            except gspread.exceptions.APIError as e:
                if delay is None or e.response.status_code not in self.RETRY_STATUS:
                    raise
                reason = f'HTTP {e.response.status_code}'
            except (requests.ConnectionError, requests.Timeout) as e:
                if delay is None:
                    raise
                reason = type(e).__name__
            log.warning('Google Sheets %s; retrying in %ss (attempt %s)', reason, delay, attempt + 1)
            time.sleep(delay)


def client():
    import settings as project_settings
    return gspread.service_account_from_dict(json.loads(project_settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT),
                                             http_client=RetryingHTTPClient)


def folder_id():
    value = os.environ.get('GSHEETS_FOLDER_ID', '').strip().strip('"').strip("'")
    # Accept a pasted link: https://drive.google.com/drive/folders/<id>
    return value.rstrip('/').split('/')[-1].split('?')[0] if value else ''


def spreadsheet_for(gc, key, registry):
    """Open the dataset's spreadsheet, creating it in the Shared Drive the first time."""
    if registry.get(key):
        try:
            return gc.open_by_key(registry[key])
        except gspread.SpreadsheetNotFound:
            log.warning('Spreadsheet for %s was deleted or unshared; creating a new one', key)
    folder = folder_id()
    if not folder:
        raise RuntimeError('GSHEETS_FOLDER_ID is not set: add the Shared Drive (or folder) ID to .env')
    title = DATASETS[key][0]
    try:
        book = gc.create(title, folder_id=folder)
        log.info('Created spreadsheet %r: %s', book.title, book.url)
    except gspread.exceptions.APIError as e:
        if 'quota' not in str(e).lower():
            raise
        # In a normal (My Drive) folder the service account cannot own files. Claim a
        # spreadsheet a person created there instead: one with this title, else a blank one.
        book = _claim_from_folder(gc, folder, title, registry)
    registry[key] = book.id
    save_registry(registry)
    return book


def _claim_from_folder(gc, folder, title, registry):
    taken = set(registry.values())
    files = [f for f in gc.list_spreadsheet_files(folder_id=folder) if f['id'] not in taken]
    match = next((f for f in files if f['name'] == title), None) or next(
        (f for f in files if f['name'].lower().startswith('untitled')), None)
    if match is None:
        raise RuntimeError(f'No spare spreadsheet in the folder for {title!r}: create a blank Google '
                           f'Sheet in the folder (any name) and run again')
    book = gc.open_by_key(match['id'])
    if book.title != title:
        book.update_title(title)
    log.info('Using spreadsheet %r from the folder: %s', title, book.url)
    return book


def _truncate(frame):
    def cut(value):
        return value[:CELL_LIMIT] + '...[truncated]' if isinstance(value, str) and len(value) > CELL_LIMIT else value
    return frame.apply(lambda column: column.map(cut)) if not frame.empty else frame


def _worksheet(book, title, frame):
    rows, cols = max(len(frame) + 1, 2), max(len(frame.columns), 1)
    try:
        ws = book.worksheet(title)
        if ws.row_count < rows or ws.col_count < cols:
            ws.resize(rows=max(ws.row_count, rows), cols=max(ws.col_count, cols))
        return ws
    except gspread.WorksheetNotFound:
        return book.add_worksheet(title=title, rows=rows, cols=cols)


SPREADSHEET_CELL_LIMIT = 10_000_000


def publish(book, tabs):
    # Publishing stages a full copy of every tab before swapping it in, so a spreadsheet
    # needs room for twice its data. Warn well before Google's 10M-cell ceiling.
    cells = sum((len(f) + 1) * max(len(f.columns), 1) for f in tabs.values())
    if cells * 2 > SPREADSHEET_CELL_LIMIT * 0.8:
        log.warning('%r holds %s cells; publishing needs %s of Google\'s 10M limit. '
                    'Split older rows into an archive sheet soon.', book.title, f'{cells:,}', f'{cells * 2:,}')
    replacements = [(_worksheet(book, title, frame), _truncate(frame)) for title, frame in tabs.items()]
    replace_worksheets(book, replacements)
    # A new spreadsheet starts with an empty 'Sheet1'; drop it once real tabs exist.
    for ws in book.worksheets():
        if ws.title in ('Sheet1', 'Sheet 1') and ws.title not in tabs and len(book.worksheets()) > 1:
            book.del_worksheet(ws)


def _track_access(gc, key, book):
    """Record in activity.db who can open this spreadsheet (and notice who was added or removed)."""
    try:
        activity_log.record_access(key, book.id, gc.list_permissions(book.id))
    except Exception as e:
        log.warning('Could not read who has access to %s: %s', key, e)


def publish_all(only=None):
    """Create any missing spreadsheets and refresh them. Returns {key: url}; raises if any failed."""
    gc, registry, links, failures = client(), load_registry(), {}, []
    for key, (title, build) in DATASETS.items():
        if only and key not in only:
            continue
        book = None
        try:
            book = spreadsheet_for(gc, key, registry)
            links[key] = book.url
            _track_access(gc, key, book)
            if build is None:
                continue
            tabs = build()
            publish(book, tabs)
            activity_log.record_share(key, title, book.url, {t: len(f) for t, f in tabs.items()})
            log.info('Published %-15s %s', key, ', '.join(f'{t}: {len(f)} rows' for t, f in tabs.items()))
        except Exception as e:
            failures.append(f'{key}: {e}')
            activity_log.record_share(key, title, book.url if book else '', status='failed', error=e)
            log.error('Failed to publish %s: %s', key, e)
    if failures:
        raise RuntimeError('; '.join(failures))
    return links


def main(argv=None):
    load_dotenv(PROJECT_DIR / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description='Publish each dataset to its own Google Spreadsheet')
    parser.add_argument('--only', nargs='+', choices=list(DATASETS), help='Publish only these datasets')
    parser.add_argument('--links', action='store_true', help='Print the spreadsheet links and exit')
    args = parser.parse_args(argv)
    if args.links:
        registry = load_registry()
        for key, (title, _) in DATASETS.items():
            sheet_id = registry.get(key)
            print(f"{title:40} {f'https://docs.google.com/spreadsheets/d/{sheet_id}' if sheet_id else '(not created yet)'}")
        return 0
    links = publish_all(args.only)
    print()
    for key, url in links.items():
        print(f'{DATASETS[key][0]:40} {url}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
