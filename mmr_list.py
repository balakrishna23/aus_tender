#!/usr/bin/env python3
"""NIAA's list of companies with active MMR contracts, linked to AusTender suppliers.

The list (Indigenous Procurement Policy, "IPP companies with active MMR contracts") gives only
a company name and how many MMR contracts it holds: no ABN, contract id, value or end date.
This script links each name to supplier ABNs in the contract API data so the prime / MMR
engine can work from ABNs. Name matches are suggestions until a person confirms them.

Tables in databases/organisations.db:
  mmr_list     the list exactly as received, one row per company per list date. Never overwritten.
  mmr_matches  company -> supplier ABN suggestions with contract counts, and the review decision
               (confirmed / rejected), which re-matching never changes.

Usage:
  python mmr_list.py --load inputs/mmr/ipp-companies-active-mmr-contracts-0.xlsx [--date YYYY-MM-DD]
  python mmr_list.py --match                      # (re)match the latest list to contract suppliers
  python mmr_list.py --export inputs/mmr/review.csv
  python mmr_list.py --review inputs/mmr/review.csv   # save the review column back
"""
import argparse
import csv
import logging
import re
import sqlite3
import sys
from collections import defaultdict
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import openpyxl

from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('mmr_list')

MMR_THRESHOLD = 7_500_000  # MMRs apply to contracts of $7.5m or more in the listed industries
REVIEW_VALUES = {'confirmed', 'rejected'}
LEGAL = r'PTY|LTD|LIMITED|PROPRIETARY|INC|INCORPORATED|CORPORATION|CORP|COMPANY|THE'
GENERIC = r'AUSTRALIA|AUSTRALIAN|AUST|AUSTRALASIA|GROUP|HOLDINGS|SERVICES|TRUSTEE|FOR|TRUST|AS|ATF'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS mmr_list (
    list_date TEXT NOT NULL,
    organisation TEXT NOT NULL,
    contract_count INTEGER,
    source_file TEXT, sheet TEXT, row_no INTEGER, loaded_at TEXT,
    PRIMARY KEY (list_date, organisation)
);
CREATE TABLE IF NOT EXISTS mmr_matches (
    organisation TEXT NOT NULL,
    supplier_abn TEXT NOT NULL,
    supplier_names TEXT,
    match_method TEXT,
    contracts INTEGER, ict_contracts INTEGER, labour_hire_contracts INTEGER,
    active_contracts INTEGER, active_over_threshold INTEGER,
    active_value REAL, latest_end_date TEXT,
    matched_at TEXT,
    review TEXT, reviewed_at TEXT, note TEXT,
    PRIMARY KEY (organisation, supplier_abn)
);
'''
STATS = ['contracts', 'ict_contracts', 'labour_hire_contracts', 'active_contracts', 'active_over_threshold',
         'active_value', 'latest_end_date']
EXPORT_COLUMNS = ['organisation', 'mmr_contracts', 'supplier_abn', 'supplier_names', 'match_method'] + STATS + [
    'review', 'note']


def connect():
    path = database_directory() / 'organisations.db'
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    return conn


def abn(value):
    digits = re.sub(r'\D', '', value or '')
    return digits if len(digits) == 11 else None


def name_keys(name):
    """(strict, loose) match keys: strict drops punctuation and legal suffixes; loose also drops
    generic words such as AUSTRALIA or SERVICES. Bracketed parts like "(ACT)" are kept in strict."""
    text = (name or '').upper().replace('&', ' AND ')
    text = re.sub(r'[^A-Z0-9() ]', ' ', text)
    strict = ' '.join(re.sub(rf'\b({LEGAL})\b', ' ', text.replace('(', ' ').replace(')', ' ')).split())
    loose = re.sub(r'\(.*?\)', ' ', text)
    loose = ' '.join(re.sub(rf'\b({LEGAL}|{GENERIC})\b', ' ', loose).split())
    return strict, loose


def load(conn, path, list_date=None):
    """Store the list as received. A company already held for that list date is left unchanged."""
    path = Path(path)
    list_date = list_date or date.fromtimestamp(path.stat().st_mtime).isoformat()
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows = sheet.iter_rows(values_only=True)
        header = [str(cell or '').strip().lower() for cell in next(rows)]
        if not header or not header[0].startswith('organisation'):
            raise ValueError(f'{path.name}: expected an "Organisation" column first, found {header}')
        loaded_at = datetime.now().isoformat(timespec='seconds')
        added = 0
        for row_no, row in enumerate(rows, start=2):
            organisation = str(row[0] or '').strip()
            if not organisation:
                continue
            count = row[1] if len(row) > 1 else None
            added += conn.execute(
                'INSERT OR IGNORE INTO mmr_list VALUES (?, ?, ?, ?, ?, ?, ?)',
                (list_date, organisation, int(count) if count not in (None, '') else None, path.name, sheet.title,
                 row_no, loaded_at)).rowcount
    finally:
        workbook.close()
    conn.commit()
    log.info('%s: %s companies added for list date %s', path.name, added, list_date)
    return list_date, added


def latest_list(conn):
    list_date = conn.execute('SELECT MAX(list_date) FROM mmr_list').fetchone()[0]
    return list_date, conn.execute('SELECT organisation, contract_count FROM mmr_list WHERE list_date = ? '
                                   'ORDER BY organisation', (list_date,)).fetchall()


def supplier_stats(contracts_db, today):
    """ABN -> names seen and contract counts, from the contract API's rolled-up contracts."""
    stats = defaultdict(lambda: {'names': set(), **{k: 0 for k in STATS}, 'latest_end_date': None})
    with closing(sqlite3.connect(f'file:{contracts_db}?mode=ro', uri=True)) as source:
        for name, raw_abn, family, value, end in source.execute(
                'SELECT supplier, supplier_abn, category_family, current_value, current_end_date FROM contracts'):
            key = abn(raw_abn)
            if not key:
                continue
            row = stats[key]
            if name and name.strip():
                row['names'].add(name.strip())
            row['contracts'] += 1
            row['ict_contracts'] += family == 'ICT services'
            row['labour_hire_contracts'] += family == 'labour hire and recruitment'
            if end and end >= today:
                row['active_contracts'] += 1
                row['active_value'] += value or 0
                row['active_over_threshold'] += (value or 0) >= MMR_THRESHOLD
            if end and (row['latest_end_date'] or '') < end:
                row['latest_end_date'] = end
    return stats


def match(conn, contracts_db=None, today=None):
    """Suggest supplier ABNs for every company on the latest list. Counts are refreshed;
    review decisions are kept. Returns (companies matched, companies on the list)."""
    contracts_db = contracts_db or database_directory() / 'contract_api.db'
    today = today or date.today().isoformat()
    list_date, companies = latest_list(conn)
    if not companies:
        raise SystemExit('No MMR list loaded yet: run with --load first')
    stats = supplier_stats(contracts_db, today)
    strict_index, loose_index = defaultdict(set), defaultdict(set)
    for key, row in stats.items():
        for name in row['names']:
            strict, loose = name_keys(name)
            strict_index[strict].add(key)
            loose_index[loose].add(key)
    matched_at = datetime.now().isoformat(timespec='seconds')
    matched = 0
    for company in companies:
        strict, loose = name_keys(company['organisation'])
        found = {key: 'name' for key in strict_index.get(strict, ())}
        if not found and len(loose) > 3:
            found = {key: 'loose name' for key in loose_index.get(loose, ())}
        # Keep historical suggestions for audit, but retire unreviewed alternatives
        # when current source data supplies a stronger exact-name match. Human
        # decisions are never replaced by re-matching.
        for old in conn.execute('SELECT supplier_abn, review FROM mmr_matches WHERE organisation = ?',
                                (company['organisation'],)).fetchall():
            if old['supplier_abn'] not in found and not old['review']:
                conn.execute("UPDATE mmr_matches SET match_method = 'superseded suggestion', matched_at = ? "
                             'WHERE organisation = ? AND supplier_abn = ?',
                             (matched_at, company['organisation'], old['supplier_abn']))
        matched += bool(found)
        for key, method in found.items():
            row = stats[key]
            values = [', '.join(sorted(row['names'])), method] + [row[k] for k in STATS] + [matched_at]
            conn.execute(
                'INSERT INTO mmr_matches (organisation, supplier_abn, supplier_names, match_method, '
                + ', '.join(STATS) + ', matched_at) VALUES (' + ', '.join('?' * (len(STATS) + 5)) + ') '
                'ON CONFLICT (organisation, supplier_abn) DO UPDATE SET supplier_names = excluded.supplier_names, '
                'match_method = excluded.match_method, matched_at = excluded.matched_at, '
                + ', '.join(f'{k} = excluded.{k}' for k in STATS),
                [company['organisation'], key] + values)
    conn.commit()
    log.info('MMR list %s: %s of %s companies matched to a supplier ABN', list_date, matched, len(companies))
    return matched, len(companies)


def export(conn, path):
    """One row per suggested ABN, plus a blank-ABN row for each company not matched yet."""
    list_date, companies = latest_list(conn)
    with open(path, 'w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, EXPORT_COLUMNS)
        writer.writeheader()
        for company in companies:
            rows = conn.execute('SELECT * FROM mmr_matches WHERE organisation = ? ORDER BY contracts DESC',
                                (company['organisation'],)).fetchall()
            for row in rows or [{}]:
                out = {k: (dict(row).get(k) if row else None) for k in EXPORT_COLUMNS}
                out.update(organisation=company['organisation'], mmr_contracts=company['contract_count'])
                writer.writerow(out)
    log.info('Wrote %s (list date %s)', path, list_date)


def import_review(conn, path):
    """Save the review and note columns of an exported file. Blank review cells change nothing."""
    saved = 0
    reviewed_at = datetime.now().isoformat(timespec='seconds')
    with open(path, newline='', encoding='utf-8-sig') as handle:
        for row in csv.DictReader(handle):
            decision = (row.get('review') or '').strip().lower()
            if not decision:
                continue
            if decision not in REVIEW_VALUES:
                raise ValueError(f'{row["organisation"]}: review must be confirmed or rejected, not {decision!r}')
            saved += conn.execute('UPDATE mmr_matches SET review = ?, note = ?, reviewed_at = ? '
                                  'WHERE organisation = ? AND supplier_abn = ?',
                                  (decision, (row.get('note') or '').strip() or None, reviewed_at,
                                   row['organisation'], abn(row['supplier_abn']) or '')).rowcount
    conn.commit()
    log.info('Saved %s review decisions', saved)
    return saved


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--load', metavar='XLSX', help='Store a NIAA MMR list workbook')
    parser.add_argument('--date', metavar='YYYY-MM-DD', help='List date for --load (default: file date)')
    parser.add_argument('--match', action='store_true', help='Match the latest list to contract suppliers')
    parser.add_argument('--export', metavar='CSV', help='Write the matches for review')
    parser.add_argument('--review', metavar='CSV', help='Save review decisions from an exported file')
    args = parser.parse_args(argv)
    if not any((args.load, args.match, args.export, args.review)):
        parser.error('nothing to do')
    (PROJECT_DIR / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(PROJECT_DIR / 'logs' / 'mmr_list.log', mode='a',
                                                      encoding='utf-8')])
    with closing(connect()) as conn:
        if args.load:
            load(conn, args.load, args.date)
        if args.review:
            import_review(conn, args.review)
        if args.match:
            match(conn)
        if args.export:
            export(conn, args.export)
    return 0


if __name__ == '__main__':
    sys.exit(main())
