#!/usr/bin/env python3
"""AusTender contract notices from the official open-data API (OCDS).

https://api.tenders.gov.au/ocds — published by the Department of Finance under CC BY 3.0 AU
(credit: AusTender). Contract notices only; tenders, standing offers and plans are not in it.

Two tables in databases/contract_api.db:
  contract_versions  one row per published version (the original notice and each amendment),
                     exactly as the API returned it. Never overwritten.
  contracts          one row per contract, derived from its versions: original and current
                     value and end date, amendment and extension counts, exemption. Rebuilt.

Usage:
  python austender_api.py                        # new and amended notices since the last run
  python austender_api.py --history 2020-01-01   # every contract published since then (resumable)
  python austender_api.py --active               # contracts still running, whenever published
  python austender_api.py --rollup               # rebuild the contracts table only
"""
import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

import activity_log
from database_manager import DatabaseManager
from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('austender_api')

NAME = 'contract_api'
BY_DATES = 'https://api.tenders.gov.au/ocds/findByDates/{kind}/{start}/{end}'
BY_ID = 'https://api.tenders.gov.au/ocds/findById/{cn_id}'
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; IllouraTenderFeed/1.0)', 'Accept': 'application/json'}
SYDNEY = ZoneInfo('Australia/Sydney')
PAUSE = 0.3  # seconds between pages: about 3 requests a second at most
# Appendix A item 16: direct purchase from Indigenous SMEs ("App A: 16. ...").
INDIGENOUS_EXEMPTION = re.compile(r'app(?:endix)?\s*a\s*[:.\-]?\s*(?:item\s*)?16\b', re.I)
# UNSPSC families (Illoura spec, section 13); longest prefix wins.
FAMILIES = {'801116': 'labour hire and recruitment', '801117': 'labour hire and recruitment',
            '8111': 'ICT services', '8116': 'ICT services', '4323': 'ICT services'}

ROLLUP_SCHEMA = '''
CREATE TABLE IF NOT EXISTS contracts (
    cn_id TEXT PRIMARY KEY,
    buyer TEXT, buyer_abn TEXT,
    supplier TEXT, supplier_abn TEXT, supplier_state TEXT, supplier_postcode TEXT,
    description TEXT, agency_reference TEXT,
    unspsc TEXT, category_family TEXT,
    procurement_method TEXT, exemption TEXT, is_indigenous_exemption INTEGER,
    limited_tender_reason TEXT,
    original_value REAL, current_value REAL,
    start_date TEXT, original_end_date TEXT, current_end_date TEXT,
    amendment_count INTEGER, extension_count INTEGER,
    first_published TEXT, last_amended TEXT,
    contact_name TEXT, contact_email TEXT, contact_phone TEXT, contact_branch TEXT, contact_division TEXT,
    source_url TEXT, retrieved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_contracts_end ON contracts(current_end_date);
CREATE INDEX IF NOT EXISTS idx_contracts_buyer ON contracts(buyer);
CREATE INDEX IF NOT EXISTS idx_contracts_supplier_abn ON contracts(supplier_abn);
CREATE INDEX IF NOT EXISTS idx_contracts_unspsc ON contracts(unspsc);
'''


# ── Fetching ──────────────────────────────────────────────────────────────────
def get_json(url, http=requests, attempts=6):
    """GET with patient retries: the API is shared, so back off on errors and rate limits."""
    for attempt in range(attempts):
        try:
            response = http.get(url, headers=HEADERS, timeout=90)
            if response.status_code == 200:
                return response.json()
            if response.status_code not in (429, 500, 502, 503, 504):
                raise RuntimeError(f'HTTP {response.status_code} for {url}: {response.text[:200]}')
            reason = f'HTTP {response.status_code}'
        except (requests.RequestException, ValueError) as e:
            reason = str(e)
        wait = min(120, 5 * 2 ** attempt)
        log.warning('%s; retrying in %ss (%s/%s)', reason, wait, attempt + 1, attempts)
        time.sleep(wait)
    raise RuntimeError(f'Gave up on {url} after {attempts} attempts')


def iso(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%SZ')


def walk(kind, start, end, http=requests, sleep=time.sleep):
    """Every release the API returns for one date window, following its page cursor."""
    url = BY_DATES.format(kind=kind, start=iso(start), end=iso(end))
    while url:
        page = get_json(url, http)
        yield from page.get('releases') or []
        url = (page.get('links') or {}).get('next')
        if url:
            sleep(PAUSE)


# ── One release -> one row ────────────────────────────────────────────────────
def sydney_day(text):
    """'2023-05-04T14:00:00Z' -> '2023-05-05' (Canberra/Sydney date, as AusTender shows it)."""
    if not text:
        return None
    moment = datetime.fromisoformat(text.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(SYDNEY).date().isoformat()


def _amount(value):
    try:
        return float((value or {}).get('amount'))
    except (TypeError, ValueError):
        return None


def _abn(party):
    return next((i.get('id') for i in party.get('additionalIdentifiers') or [] if i.get('scheme') == 'AU-ABN'), None)


def version_row(release, retrieved_at):
    """Flatten one OCDS release (a contract notice or one amendment) into a row."""
    contract = (release.get('contracts') or [{}])[0]
    tender = release.get('tender') or {}
    parties = release.get('parties') or []
    buyer = next((p for p in parties if 'procuringEntity' in (p.get('roles') or [])), {})
    supplier = next((p for p in parties if 'supplier' in (p.get('roles') or [])), {})
    # AusTender puts the agency's contact officer on the supplier party; the buyer party only
    # carries a generic tenders@ address. Take the first contact that names a person or team.
    contact = next((p.get('contactPoint') for p in parties if (p.get('contactPoint') or {}).get('name')), {}) or {}
    amendments = contract.get('amendments') or []
    notice_id = amendments[-1].get('id') if amendments else contract.get('id')
    number = re.search(r'-A(\d+)$', notice_id or '')
    address = supplier.get('address') or {}
    period = contract.get('period') or {}
    item = (contract.get('items') or [{}])[0]
    return {
        'release_id': release.get('id'), 'ocid': release.get('ocid'),
        'cn_id': contract.get('id'), 'notice_id': notice_id,
        'amendment_no': int(number.group(1)) if number else 0,
        'tag': ','.join(release.get('tag') or []),
        'release_date': release.get('date'), 'published_on': sydney_day(release.get('date')),
        'date_signed': sydney_day(contract.get('dateSigned')),
        'start_date': sydney_day(period.get('startDate')), 'end_date': sydney_day(period.get('endDate')),
        'value': _amount(contract.get('value')), 'currency': (contract.get('value') or {}).get('currency'),
        'description': contract.get('description'), 'agency_reference': contract.get('title'),
        'unspsc': (item.get('classification') or {}).get('id'),
        'procurement_method': tender.get('procurementMethodDetails') or tender.get('procurementMethod'),
        'limited_tender_exempt': tender.get('limitedTenderExempt'),
        'exemption_code': tender.get('exemptionCode'), 'exemption': tender.get('exemption'),
        'limited_tender_reason_code': tender.get('limitedTenderReasonCode'),
        'limited_tender_reason': tender.get('limitedTenderReason'),
        'buyer': buyer.get('name'), 'buyer_abn': _abn(buyer),
        'supplier': supplier.get('name'), 'supplier_abn': _abn(supplier),
        'supplier_street': address.get('streetAddress'), 'supplier_locality': address.get('locality'),
        'supplier_region': address.get('region'), 'supplier_postcode': address.get('postalCode'),
        'supplier_country': address.get('countryName'),
        'contact_name': contact.get('name'), 'contact_email': contact.get('email'),
        'contact_phone': contact.get('telephone'), 'contact_branch': contact.get('branch'),
        'contact_division': contact.get('division'),
        'source_url': BY_ID.format(cn_id=contract.get('id')),
        'raw_json': json.dumps(release, separators=(',', ':')),
        'retrieved_at': retrieved_at,
    }


# ── Storage ───────────────────────────────────────────────────────────────────
def connect():
    dbm = DatabaseManager()
    dbm.initialize_database(NAME)
    conn = sqlite3.connect(dbm.get_db_path(NAME), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(ROLLUP_SCHEMA)
    return conn


def save(conn, releases, retrieved_at):
    """Store versions not held yet; a version already held is left exactly as it was.
    Returns (the new rows, contract ids in this batch)."""
    rows = {r['release_id']: r for r in (version_row(x, retrieved_at) for x in releases
                                        if x.get('id') and x.get('contracts'))}
    if not rows:
        return [], set()
    held = {r[0] for r in conn.execute(f"SELECT release_id FROM contract_versions WHERE release_id IN "
                                       f"({','.join('?' * len(rows))})", list(rows))}
    new = [r for key, r in rows.items() if key not in held]
    if new:
        columns = list(new[0])
        conn.executemany(f"INSERT INTO contract_versions ({', '.join(columns)}) "
                         f"VALUES ({', '.join(':' + c for c in columns)})", new)
        conn.commit()
    return new, {r['cn_id'] for r in rows.values()}


def get_state(conn, key):
    row = conn.execute('SELECT value FROM crawl_state WHERE key = ?', (key,)).fetchone()
    return row[0] if row else None


def set_state(conn, key, value):
    conn.execute('INSERT INTO crawl_state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET '
                 'value = excluded.value, updated_at = CURRENT_TIMESTAMP', (key, value))
    conn.commit()


# ── One record per contract ───────────────────────────────────────────────────
def family(unspsc):
    code = str(unspsc or '')
    return next((name for prefix, name in sorted(FAMILIES.items(), key=lambda kv: -len(kv[0]))
                 if code.startswith(prefix)), None)


def rollup_one(versions):
    """Original and current state of one contract from its versions (dicts)."""
    ordered = sorted(versions, key=lambda v: (v['amendment_no'], v['release_date'] or ''))
    first, last = ordered[0], ordered[-1]
    latest = lambda field: next((v[field] for v in reversed(ordered) if v[field]), None)  # noqa: E731
    ends = [v['end_date'] for v in ordered]
    extensions = sum(1 for a, b in zip(ends, ends[1:]) if a and b and b > a)
    exemption = latest('exemption')
    return {
        'cn_id': first['cn_id'], 'buyer': latest('buyer'), 'buyer_abn': latest('buyer_abn'),
        'supplier': latest('supplier'), 'supplier_abn': latest('supplier_abn'),
        'supplier_state': latest('supplier_region'), 'supplier_postcode': latest('supplier_postcode'),
        'description': first['description'] or latest('description'),
        'agency_reference': latest('agency_reference'),
        'unspsc': latest('unspsc'), 'category_family': family(latest('unspsc')),
        'procurement_method': latest('procurement_method'), 'exemption': exemption,
        'is_indigenous_exemption': int(bool(exemption and INDIGENOUS_EXEMPTION.search(exemption))),
        'limited_tender_reason': latest('limited_tender_reason'),
        'original_value': first['value'], 'current_value': last['value'],
        'start_date': first['start_date'], 'original_end_date': first['end_date'],
        'current_end_date': last['end_date'],
        'amendment_count': sum(1 for v in ordered if v['amendment_no'] > 0), 'extension_count': extensions,
        'first_published': first['published_on'],
        'last_amended': last['published_on'] if last['amendment_no'] else None,
        'contact_name': latest('contact_name'), 'contact_email': latest('contact_email'),
        'contact_phone': latest('contact_phone'), 'contact_branch': latest('contact_branch'),
        'contact_division': latest('contact_division'),
        'source_url': first['source_url'], 'retrieved_at': latest('retrieved_at'),
    }


ROLLUP_FIELDS = ['cn_id', 'buyer', 'buyer_abn', 'supplier', 'supplier_abn', 'supplier_region', 'supplier_postcode',
                 'description', 'agency_reference', 'unspsc', 'procurement_method', 'exemption',
                 'limited_tender_reason', 'value', 'start_date', 'end_date', 'amendment_no', 'release_date',
                 'published_on', 'contact_name', 'contact_email', 'contact_phone', 'contact_branch',
                 'contact_division', 'source_url', 'retrieved_at']


def rollup(conn, cn_ids=None):
    """Rebuild the contracts table (all of it, or only `cn_ids`). Returns rows written."""
    where, params = '', ()
    if cn_ids is not None:
        cn_ids = sorted(cn_ids)
        if not cn_ids:
            return 0
        conn.execute('CREATE TEMP TABLE IF NOT EXISTS touched (cn_id TEXT PRIMARY KEY)')
        conn.execute('DELETE FROM touched')
        conn.executemany('INSERT OR IGNORE INTO touched VALUES (?)', [(c,) for c in cn_ids])
        where = 'WHERE cn_id IN (SELECT cn_id FROM touched)'
    cursor = conn.execute(f"SELECT {', '.join(ROLLUP_FIELDS)} FROM contract_versions {where} "
                          'ORDER BY cn_id, amendment_no, release_date', params)
    written, group, current = 0, [], None
    batch = []

    def flush():
        nonlocal written
        if batch:
            columns = list(batch[0])
            conn.executemany(f"INSERT OR REPLACE INTO contracts ({', '.join(columns)}) "
                             f"VALUES ({', '.join(':' + c for c in columns)})", batch)
            written += len(batch)
            batch.clear()
    for row in cursor:
        row = dict(row)
        if row['cn_id'] != current and group:
            batch.append(rollup_one(group))
            group = []
            if len(batch) >= 5000:
                flush()
        current = row['cn_id']
        group.append(row)
    if group:
        batch.append(rollup_one(group))
    flush()
    conn.commit()
    return written


# ── Runs ──────────────────────────────────────────────────────────────────────
def months(start, end):
    first = date(start.year, start.month, 1)
    while first <= end:
        following = date(first.year + (first.month == 12), first.month % 12 + 1, 1)
        yield first, min(following - timedelta(days=1), end)
        first = following


def collect(conn, kind, start, end, label, http=requests, sleep=time.sleep):
    """Fetch one window (UTC day bounds), save it, roll up the contracts it touched."""
    begin = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    finish = datetime(end.year, end.month, end.day, 23, 59, 59, tzinfo=timezone.utc)
    retrieved = datetime.now().isoformat(timespec='seconds')
    new, touched, seen, batch = [], set(), 0, []
    for release in walk(kind, begin, finish, http, sleep):
        batch.append(release)
        if len(batch) >= 500:
            added, ids = save(conn, batch, retrieved)
            new, touched, seen, batch = new + added, touched | ids, seen + len(batch), []
    added, ids = save(conn, batch, retrieved)
    new, touched, seen = new + added, touched | ids, seen + len(batch)
    rollup(conn, touched)
    log.info('%s: %s versions received, %s new, %s contracts updated', label, seen, len(new), len(touched))
    return new, touched


def run_history(conn, since, http=requests, sleep=time.sleep):
    """Every contract published since `since`, a month at a time. A finished month is skipped
    when the command is run again; the last two months are always re-read (new amendments)."""
    today = date.today()
    total = 0
    for start, end in months(since, today):
        key = f'history:{start:%Y-%m}'
        recent = (today - end).days < 62
        if get_state(conn, key) == 'done' and not recent:
            continue
        new, _ = collect(conn, 'contractPublished', start, end, f'Published {start:%b %Y}', http, sleep)
        total += len(new)
        set_state(conn, key, 'done')
    return total


def run_active(conn, http=requests, sleep=time.sleep):
    """Contracts that end today or later, whenever they were published (catches long contracts
    published before the history start)."""
    today, total = date.today(), 0
    for year in range(today.year, today.year + 16):
        start, end = max(today, date(year, 1, 1)), date(year, 12, 31)
        new, _ = collect(conn, 'contractEnd', start, end, f'Ending in {year}', http, sleep)
        total += len(new)
    return total


def run_incremental(conn, http=requests, sleep=time.sleep):
    """New and amended notices (any contract) since the last run, with two days of overlap."""
    today = date.today()
    last = get_state(conn, 'modified_through')
    start = (date.fromisoformat(last) - timedelta(days=2)) if last else today - timedelta(days=7)
    new, touched = collect(conn, 'contractLastModified', start, today, f'Modified {start} to {today}',
                           http, sleep)
    set_state(conn, 'modified_through', today.isoformat())
    activity_log.record_items([(NAME, 'new' if not r['amendment_no'] else 'updated', r['notice_id'],
                                r['description'], r['source_url']) for r in new])
    return len(new)


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--history', metavar='YYYY-MM-DD', help='Every contract published since this date')
    parser.add_argument('--active', action='store_true', help='Every contract that has not ended yet')
    parser.add_argument('--rollup', action='store_true', help='Only rebuild the contracts table')
    args = parser.parse_args(argv)
    (PROJECT_DIR / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(PROJECT_DIR / 'logs' / f'{NAME}.log', mode='a',
                                                      encoding='utf-8')])
    started = time.time()
    with closing(connect()) as conn:
        if args.rollup:
            log.info('Rolled up %s contracts', rollup(conn))
            return 0
        new = 0
        if args.history:
            new += run_history(conn, date.fromisoformat(args.history))
        if args.active:
            new += run_active(conn)
        if not args.history and not args.active:
            new += run_incremental(conn)
        versions, contracts = (conn.execute('SELECT COUNT(*) FROM contract_versions').fetchone()[0],
                               conn.execute('SELECT COUNT(*) FROM contracts').fetchone()[0])
    # The batch runner reads this line from the log for its run report.
    log.info('%s total notices saved to %s (%s versions, %s contracts, %.0fs)', new,
             database_directory() / (NAME + '.db'), versions, contracts, time.time() - started)
    return 0


if __name__ == '__main__':
    sys.exit(main())
