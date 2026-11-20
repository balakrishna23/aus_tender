#!/usr/bin/env python3
"""State government contract awards from open-data portals (no API key), in databases/state_contracts.db.

- WA: Tenders WA "Contract Award Details" yearly CSVs on catalogue.data.wa.gov.au (CKAN; no robots.txt).
  CC BY 4.0, credit "Department of Treasury and Finance (WA)". Fetched automatically; a file whose
  size and modified date are unchanged since it was loaded is skipped.
- QLD: agency contract disclosure reports (contracts over $10,000) on data.qld.gov.au, CC BY 4.0.
  Not fetched: its robots.txt disallows /api/ and its file downloads sit behind a bot challenge
  (checked 2026-10-19). Save the CSV/XLSX files from a browser into inputs/state_contracts/qld/
  and import them. Column names differ by agency, so the common fields are matched by header
  words and every row is also kept whole in raw_json.

Usage:
  python state_contracts.py                                   # WA
  python state_contracts.py --import inputs/state_contracts/qld   # QLD files saved by hand
"""
import argparse
import csv
import io
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

import classify
from project_paths import PROJECT_DIR, database_directory

WA_SEARCH = 'https://catalogue.data.wa.gov.au/api/3/action/package_search'
WA_TITLE = re.compile(r'tenders wa .*contract.*(award|details)', re.I)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; IllouraTenderFeed/1.0)'}
WA_COLUMNS = {
    'Public_Authority', 'Title', 'Description', 'Reference_Number', 'Procurement_Method',
    'UNSPSC_Code_1', 'Award_Date', 'Commencement_Date', 'Final_Expiry_Date',
    'Initial_Expiry_Date', 'Original_Contract_Value', 'Supplier_Name', 'Supplier_Address',
    'CUA_Number', 'Panel_Contract',
}
QLD_INBOX = PROJECT_DIR / 'inputs' / 'state_contracts' / 'qld'

SCHEMA = '''
CREATE TABLE IF NOT EXISTS contracts (
    state TEXT NOT NULL, source_file TEXT NOT NULL, row_no INTEGER NOT NULL,
    agency TEXT, title TEXT, description TEXT, reference TEXT, procurement_method TEXT, unspsc TEXT,
    award_date TEXT, start_date TEXT, end_date TEXT, value REAL, supplier TEXT, supplier_abn TEXT,
    supplier_address TEXT, panel TEXT, is_ict INTEGER, relevance_reason TEXT, category_family TEXT,
    raw_json TEXT, source_url TEXT, loaded_at TEXT,
    PRIMARY KEY (state, source_file, row_no));
CREATE INDEX IF NOT EXISTS contracts_supplier ON contracts(supplier);
CREATE INDEX IF NOT EXISTS contracts_abn ON contracts(supplier_abn);
CREATE TABLE IF NOT EXISTS sources (
    source_file TEXT PRIMARY KEY, state TEXT, title TEXT, url TEXT, licence TEXT, publisher TEXT,
    modified TEXT, size INTEGER, rows INTEGER, loaded_at TEXT);
'''

# Header words for QLD reports (first match wins, so the more specific words come first).
QLD_FIELDS = {
    'supplier_abn': ('abn',),
    'supplier': ('supplier name', 'name of supplier', 'supplier', 'contractor', 'vendor', 'awarded to'),
    'supplier_address': ('supplier address', 'address', 'postcode', 'suburb'),
    'agency': ('agency', 'department', 'entity', 'organisation'),
    'value': ('contract value', 'value', 'amount', 'price'),
    'award_date': ('award date', 'date awarded', 'award'),
    'start_date': ('start', 'commence'),
    'end_date': ('end date', 'expiry', 'finish'),
    'procurement_method': ('procurement method', 'method', 'procurement'),
    'reference': ('reference', 'contract number', 'contract no'),
    'title': ('title', 'contract name', 'name of contract'),
    'description': ('description', 'purpose', 'details', 'goods', 'services'),
    'unspsc': ('unspsc',),
}


def connect():
    database_directory().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(database_directory() / 'state_contracts.db')
    conn.executescript(SCHEMA)
    return conn


def iso_date(text):
    """'21/02/2024', '2024-02-21', '21-Feb-2024' or an Excel date -> '2024-02-21'; else None."""
    if isinstance(text, (datetime, date)):
        return text.strftime('%Y-%m-%d')
    text = str(text or '').strip()
    if re.fullmatch(r'\d{5}(\.0+)?', text):  # Excel serial day
        return (date(1899, 12, 30) + timedelta(days=int(float(text)))).isoformat()
    for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%b-%Y', '%d %b %Y', '%d %B %Y', '%d/%m/%y', '%Y-%m-%d %H:%M:%S',
                '%d/%m/%Y %H:%M', '%d/%m/%Y %I:%M:%S %p'):
        try:
            return datetime.strptime(text, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def amount(text):
    if isinstance(text, (int, float)):
        return float(text)
    cleaned = re.sub(r'[^\d.\-]', '', str(text or ''))
    try:
        return float(cleaned) if cleaned not in ('', '.', '-') else None
    except ValueError:
        return None


def abn(text):
    digits = re.sub(r'\D', '', str(text or ''))
    return digits if len(digits) == 11 else None


def tag(row):
    found = classify.classify(title=row.get('title') or '', description=row.get('description') or '',
                              code=row.get('unspsc'))
    row.update(is_ict=found['is_relevant'], relevance_reason=found['relevance_reason'],
               category_family=found['category_family'])
    return row


def wa_row(raw):
    return tag(dict(
        agency=raw.get('Public_Authority'), title=raw.get('Title'), description=raw.get('Description'),
        reference=raw.get('Reference_Number'), procurement_method=raw.get('Procurement_Method'),
        unspsc=(raw.get('UNSPSC_Code_1') or '').strip() or None, award_date=iso_date(raw.get('Award_Date')),
        start_date=iso_date(raw.get('Commencement_Date')),
        end_date=iso_date(raw.get('Final_Expiry_Date') or raw.get('Initial_Expiry_Date')),
        value=amount(raw.get('Original_Contract_Value')), supplier=(raw.get('Supplier_Name') or '').strip() or None,
        supplier_abn=None, supplier_address=raw.get('Supplier_Address'),
        panel=raw.get('CUA_Number') or (raw.get('Panel_Contract') if raw.get('Panel_Contract') == 'Yes' else None)))


def match_columns(headers):
    """{field: header} for a QLD report, using the first header that contains a field's words."""
    normal = {h: re.sub(r'[^a-z0-9]+', ' ', str(h or '').lower()).strip() for h in headers}
    chosen, used = {}, set()
    for field, words in QLD_FIELDS.items():
        for word in words:
            hit = next((h for h, n in normal.items() if h not in used and re.search(rf'\b{word}\b', n)), None)
            if hit is not None:
                chosen[field] = hit
                used.add(hit)
                break
    return chosen


def qld_row(raw, columns, agency_hint):
    get = {field: raw.get(header) for field, header in columns.items()}
    text = lambda v: str(v).strip() if v not in (None, '') else None  # noqa: E731
    return tag(dict(
        agency=text(get.get('agency')) or agency_hint, title=text(get.get('title')),
        description=text(get.get('description')), reference=text(get.get('reference')),
        procurement_method=text(get.get('procurement_method')), unspsc=text(get.get('unspsc')),
        award_date=iso_date(get.get('award_date')), start_date=iso_date(get.get('start_date')),
        end_date=iso_date(get.get('end_date')), value=amount(get.get('value')), supplier=text(get.get('supplier')),
        supplier_abn=abn(get.get('supplier_abn')), supplier_address=text(get.get('supplier_address')), panel=None))


def read_table(path):
    """Rows (dicts) from a CSV or Excel file; the header is the first row with 3+ filled cells."""
    if path.suffix.lower() in ('.xlsx', '.xlsm'):
        import openpyxl
        book = openpyxl.load_workbook(path, read_only=True, data_only=True)
        grid = [list(r) for r in book.worksheets[0].iter_rows(values_only=True)]
    else:
        raw = path.read_bytes()
        text = next((raw.decode(enc) for enc in ('utf-8-sig', 'cp1252', 'latin-1') if _decodes(raw, enc)), '')
        grid = list(csv.reader(io.StringIO(text)))
    start = next((i for i, r in enumerate(grid) if sum(1 for c in r if str(c or '').strip()) >= 3), None)
    if start is None:
        return []
    headers = [str(h or '').strip() or f'column_{i + 1}' for i, h in enumerate(grid[start])]
    return [dict(zip(headers, r)) for r in grid[start + 1:] if any(str(c or '').strip() for c in r)]


def _decodes(raw, encoding):
    try:
        raw.decode(encoding)
        return True
    except UnicodeDecodeError:
        return False


def store(conn, state, source_file, rows, raws, source):
    """Replace one file's rows; the file's metadata goes to `sources`."""
    now = datetime.now().isoformat(timespec='seconds')
    with conn:
        conn.execute('DELETE FROM contracts WHERE state=? AND source_file=?', (state, source_file))
        conn.executemany(
            'INSERT INTO contracts (state, source_file, row_no, agency, title, description, reference, '
            'procurement_method, unspsc, award_date, start_date, end_date, value, supplier, supplier_abn, '
            'supplier_address, panel, is_ict, relevance_reason, category_family, raw_json, source_url, loaded_at) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [(state, source_file, i, r['agency'], r['title'], r['description'], r['reference'],
              r['procurement_method'], r['unspsc'], r['award_date'], r['start_date'], r['end_date'], r['value'],
              r['supplier'], r['supplier_abn'], r['supplier_address'], r['panel'], r['is_ict'],
              r['relevance_reason'], r['category_family'], json.dumps(raw, default=str, ensure_ascii=False),
              source.get('url'), now) for i, (r, raw) in enumerate(zip(rows, raws), start=1)])
        conn.execute('INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (source_file, state, source.get('title'), source.get('url'), source.get('licence'),
                      source.get('publisher'), source.get('modified'), source.get('size'), len(rows), now))


class PublisherChallenge(ValueError):
    """The publisher returned a bot challenge instead of data."""


def validate_wa_response(response):
    headers = {key.lower(): value for key, value in response.headers.items()}
    body = response.content
    prefix = body[:4096].lstrip(b'\xef\xbb\xbf \t\r\n').lower()
    if ('x-amzn-waf-action' in headers or
            'text/html' in headers.get('content-type', '').lower() or
            b'<html' in prefix or b'<!doctype' in prefix):
        raise PublisherChallenge('publisher bot challenge; not stored')
    if response.status_code != 200:
        raise ValueError(f'HTTP {response.status_code}; not stored')
    if not body.strip():
        raise ValueError('empty response body; not stored')


def wa_csv_rows(response):
    validate_wa_response(response)
    reader = csv.DictReader(io.StringIO(response.content.decode('utf-8-sig')), strict=True)
    headers = reader.fieldnames or []
    missing = WA_COLUMNS.difference(headers)
    if missing or len(headers) != len(set(headers)):
        raise ValueError(f'invalid WA CSV header (missing: {", ".join(sorted(missing))}); not stored')
    raws = []
    for raw in reader:
        if None in raw or any(value is None for value in raw.values()):
            raise ValueError('WA CSV row width mismatch; not stored')
        if any(value.strip() for value in raw.values()):
            raws.append(raw)
    if not raws:
        raise ValueError('WA CSV contains no data rows; not stored')
    return raws


def fetch_wa(conn, http=requests, sleep=time.sleep):
    found = http.get(WA_SEARCH, params={'q': '"Tenders WA"', 'rows': 100}, headers=HEADERS, timeout=60)
    validate_wa_response(found)
    loaded = 0
    files = []
    for package in found.json()['result']['results']:
        if not WA_TITLE.search(package['title']):
            continue
        for resource in package['resources']:
            if (resource.get('format') or '').lower().strip('.') != 'csv':
                continue
            files.append((package, resource))
    for index, (package, resource) in enumerate(files):
        name = resource['url'].rsplit('/', 1)[-1]
        modified = resource.get('last_modified') or package['metadata_modified']
        known = conn.execute('SELECT modified, size FROM sources WHERE source_file=?', (name,)).fetchone()
        held = conn.execute('SELECT COUNT(*) FROM contracts WHERE state=? AND source_file=?',
                            ('WA', name)).fetchone()[0]
        if held and known and known == (modified, resource.get('size')):
            print(f'WA {name}: unchanged, skipped')
            continue
        sleep(2)
        try:
            response = http.get(resource['url'], headers=HEADERS, timeout=300)
            raws = wa_csv_rows(response)
        except PublisherChallenge as exc:
            print(f'WA {name}: {exc}')
            pending = [r['url'].rsplit('/', 1)[-1] for _, r in files[index:]]
            print(f'WA run stopped; files not loaded: {", ".join(pending)}')
            break
        except (requests.RequestException, ValueError, csv.Error) as exc:
            print(f'WA {name}: download failed; not stored ({exc})')
            continue
        store(conn, 'WA', name, [wa_row(r) for r in raws], raws, dict(
            title=package['title'], url=resource['url'], licence=package.get('license_title'),
            publisher=(package.get('organization') or {}).get('title'), modified=modified,
            size=resource.get('size')))
        print(f'WA {name}: {len(raws)} contracts')
        loaded += len(raws)
    return loaded


def import_qld(conn, folder):
    loaded = 0
    for path in sorted(Path(folder).glob('*')):
        if path.suffix.lower() not in ('.csv', '.xlsx', '.xlsm'):
            continue
        raws = read_table(path)
        columns = match_columns(raws[0].keys()) if raws else {}
        rows = [qld_row(r, columns, agency_hint=path.stem) for r in raws]
        store(conn, 'QLD', path.name, rows, raws, dict(
            title=path.stem, url=None, licence='Creative Commons Attribution 4.0',
            publisher='Queensland Government (data.qld.gov.au)', modified=None, size=path.stat().st_size))
        missing = [f for f in ('supplier', 'value', 'award_date') if f not in columns]
        print(f'QLD {path.name}: {len(rows)} contracts' + (f' (no column found for {", ".join(missing)})'
                                                           if missing else ''))
        loaded += len(rows)
    return loaded


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--import', dest='folder', nargs='?', const=str(QLD_INBOX), metavar='FOLDER',
                        help='Import QLD contract disclosure files saved by hand (default inputs/state_contracts/qld)')
    args = parser.parse_args(argv)
    with closing(connect()) as conn:
        count = import_qld(conn, args.folder) if args.folder else fetch_wa(conn)
        totals = conn.execute('SELECT state, COUNT(*), SUM(is_ict) FROM contracts GROUP BY state').fetchall()
    print(f'{count} rows loaded; held: ' + ', '.join(f'{s} {n:,} ({ict or 0:,} ICT)' for s, n, ict in totals))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
