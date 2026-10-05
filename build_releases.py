#!/usr/bin/env python3
"""The product feed: tenders released from the feed start date onwards.

Reads the per-source databases and keeps one row per open-market opportunity in
databases/releases.db:

  austender_atm   Commonwealth approaches to market (tenders.gov.au)
  buyict          Commonwealth ICT / labour-hire RFQs (BuyICT)
  act             ACT Government tenders
  nsw             NSW Government open opportunities (buy.nsw)
  austender_plan  Planned procurements newly added to agencies' annual plans

Awards (contract notices, standing offers) are not releases and stay out of the feed.

A notice joins the feed when its publish date is on/after the feed start date
(set on the first build, or --since). Each build writes the releases first seen
in that build to exports/releases/ as JSON and CSV for delivery.

Usage:
  python build_releases.py                 # update the feed
  python build_releases.py --since 2026-10-02
"""
import argparse
import csv
import hashlib
import json
import logging
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from pathlib import Path

import activity_log
from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('releases')

FIELDS = ['source', 'source_id', 'title', 'buyer', 'category', 'notice_type', 'location',
          'published_on', 'closes_at', 'closes_on', 'status', 'description', 'contact_name',
          'contact_email', 'url']

MONTHS = {m: i for i, m in enumerate(
    ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'], 1)}


def parse_day(text):
    """'24-Sep-2026', 'Monday, 21 September 2026', 'Thu, 02 July 2026 12:30 pm' -> date."""
    match = re.search(r'(\d{1,2})[\s-]+([A-Za-z]{3})[A-Za-z]*[\s-]+(\d{4})', text or '')
    if not match or match.group(2).lower() not in MONTHS:
        match = re.search(r'(\d{4})-(\d{2})-(\d{2})', text or '')
        return date(*map(int, match.groups())) if match else None
    try:
        return date(int(match.group(3)), MONTHS[match.group(2).lower()], int(match.group(1)))
    except ValueError:
        return None


EMAIL_RE = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')


def _first_email(text):
    match = EMAIL_RE.search(text or '')
    return match.group(0).rstrip('.') if match else ''


def _contact_name(text):
    """BuyICT's contact field holds an email, a name, or a 'use the Q&A portal' notice."""
    return EMAIL_RE.sub('', text or '').strip(' ,;-')


def clean(value):
    if value is None:
        return ''
    return ' '.join(str(value).replace('�', '-').split())


def rows(db_name, sql):
    path = database_directory() / f'{db_name}.db'
    if not path.exists():
        log.warning('Missing source database %s', path)
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        except sqlite3.OperationalError as e:
            log.warning('Skipping %s: %s', db_name, e)
            return []


def collect():
    """Yield feed records from every source, normalised to FIELDS."""
    for r in rows('aus_tender', 'SELECT * FROM tenders'):
        yield dict(source='austender_atm', source_id=r['atm_id'], title=r['service_name'],
                   buyer=r['agency'], category=r['category'], notice_type=r['atm_type'],
                   location=r['location'], published=r['publish_date'],
                   closes_at=r['close_date_time'], description=r['description'],
                   contact_name=r['contact_name'], contact_email=r['contact_email'], url=r['url'],
                   closed='/ShowClosed/' in (r['url'] or ''))
    for r in rows('buy_ict', 'SELECT * FROM buy_ict_tenders'):
        yield dict(source='buyict', source_id=r['rfq_id'] or r['sys_id'], title=r['title'],
                   buyer=r['buyer'], category=r['rfq_type'], notice_type=r['rfq_type'],
                   location=r['location_of_work'], published=r['rfq_published_date'],
                   closes_at=r['rfq_closing_date'], description=r['working_arrangements'],
                   contact_name=_contact_name(r['buyer_contact']),
                   contact_email=_first_email(r['buyer_contact']), url=r['url'],
                   closed=r['status'] == 'closed')
    for r in rows('act_tender', 'SELECT * FROM act_tenders'):
        yield dict(source='act', source_id=r['reference'], title=r['title'], buyer=r['agency'],
                   category=r['category'], notice_type=r['category'], location='ACT',
                   published=r['publish_date'], closes_at=r['close_date'],
                   description=r['description'], contact_name=r['contact_name'],
                   contact_email=r['contact_email'], url=r['url'],
                   closed=(r['status'] or '').lower() != 'open')
    for r in rows('nsw_tender', "SELECT * FROM nsw_tenders WHERE portal = 'nsw_opportunity'"):
        yield dict(source='nsw', source_id=r['reference'], title=r['title'], buyer=r['agency'],
                   category=r['category'], notice_type=r['notice_type'], location=r['location'],
                   published=r['publish_date'], closes_at=r['close_date'],
                   description=r['description'], contact_name=r['contact_name'],
                   contact_email=r['contact_email'], url=r['url'],
                   closed=(r['status'] or '').lower() != 'open')
    for r in rows('planned_procurement', 'SELECT * FROM planned_procurements'):
        # Plans carry no publish date: the day we first saw the entry stands in for it.
        yield dict(source='austender_plan', source_id=r['agency_reference'] or r['url'],
                   title=r['title'], buyer=r['agency'], category=r['category'],
                   notice_type='Planned procurement', location='',
                   published=str(r['created_at'])[:10],
                   closes_at=r['estimated_date_of_approach_to_market'],
                   description=r['change_comments'], contact_name=r['contact_name'],
                   contact_email=r['contact_email'], url=r['url'], closed=False, no_close=True,
                   first_seen_utc=str(r['created_at']))


def normalise(raw, today):
    closes_on = None if raw.get('no_close') else parse_day(raw['closes_at'])
    record = {
        'source': raw['source'],
        'source_id': clean(raw['source_id']),
        'title': clean(raw['title']),
        'buyer': clean(raw['buyer']),
        'category': clean(raw['category']),
        'notice_type': clean(raw['notice_type']),
        'location': clean(raw['location']),
        'published_on': (parse_day(raw['published']) or '') and parse_day(raw['published']).isoformat(),
        'closes_at': clean(raw['closes_at']),
        'closes_on': closes_on.isoformat() if closes_on else '',
        'status': 'closed' if raw['closed'] or (closes_on and closes_on < today) else 'open',
        'description': clean(raw['description'])[:2000],
        'contact_name': clean(raw['contact_name']),
        'contact_email': clean(raw['contact_email']),
        'url': clean(raw['url']),
    }
    return record


SCHEMA = '''
CREATE TABLE IF NOT EXISTS releases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    release_key TEXT UNIQUE,
    {columns},
    content_hash TEXT,
    first_seen_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_releases_first_seen ON releases(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_releases_published ON releases(published_on);
CREATE INDEX IF NOT EXISTS idx_releases_status ON releases(status);
CREATE TABLE IF NOT EXISTS feed_state (key TEXT PRIMARY KEY, value TEXT);
'''.format(columns=',\n    '.join(f'{f} TEXT' for f in FIELDS))


def build(since=None, export_dir=None):
    """Update the feed. Returns {'new': [...], 'changed': [...], 'feed_start': ...}."""
    now = datetime.now().replace(microsecond=0).isoformat(sep=' ')
    today = date.today()
    conn = sqlite3.connect(database_directory() / 'releases.db')
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    state = dict(conn.execute('SELECT key, value FROM feed_state').fetchall())
    feed_start = since or state.get('feed_start') or today.isoformat()
    conn.execute('INSERT OR REPLACE INTO feed_state VALUES (?, ?)', ('feed_start', feed_start))
    # Sources without a publish date only count entries first scraped after the feed went live.
    started_utc = state.get('started_utc') or datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')
    conn.execute('INSERT OR IGNORE INTO feed_state VALUES (?, ?)', ('started_utc', started_utc))

    new, changed = [], []
    for raw in collect():
        if raw.get('first_seen_utc') and raw['first_seen_utc'] < started_utc:
            continue
        record = normalise(raw, today)
        if not record['published_on'] or record['published_on'] < feed_start or not record['url']:
            continue
        key = f"{record['source']}:{record['url']}"
        digest = hashlib.sha256(json.dumps(record, sort_keys=True).encode()).hexdigest()
        existing = conn.execute('SELECT content_hash FROM releases WHERE release_key = ?', (key,)).fetchone()
        if existing and existing[0] == digest:
            continue
        values = {**record, 'release_key': key, 'content_hash': digest, 'now': now}
        if existing:
            conn.execute(f"UPDATE releases SET {', '.join(f'{f} = :{f}' for f in FIELDS)}, "
                         "content_hash = :content_hash, updated_at = :now WHERE release_key = :release_key",
                         values)
            changed.append(record)
        else:
            conn.execute(f"INSERT INTO releases (release_key, {', '.join(FIELDS)}, content_hash, "
                         f"first_seen_at, updated_at) VALUES (:release_key, "
                         f"{', '.join(':' + f for f in FIELDS)}, :content_hash, :now, :now)", values)
            new.append({**record, 'first_seen_at': now})
    conn.execute('INSERT OR REPLACE INTO feed_state VALUES (?, ?)', ('last_build', now))
    activity_log.record_items(
        [('releases', 'new', r['source_id'], r['title'], r['url']) for r in new] +
        [('releases', 'updated', r['source_id'], r['title'], r['url']) for r in changed])
    conn.commit()
    total, open_count = conn.execute("SELECT COUNT(*), SUM(status = 'open') FROM releases").fetchone()
    conn.close()

    export_dir = Path(export_dir or PROJECT_DIR / 'exports' / 'releases')
    if new:
        export_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.replace(':', '').replace(' ', '-').replace('-', '')
        (export_dir / f'new_{stamp}.json').write_text(json.dumps(new, indent=1), encoding='utf-8')
        with open(export_dir / f'new_{stamp}.csv', 'w', newline='', encoding='utf-8-sig') as f:
            writer = csv.DictWriter(f, fieldnames=FIELDS + ['first_seen_at'])
            writer.writeheader()
            writer.writerows(new)
    by_source = {}
    for record in new:
        by_source[record['source']] = by_source.get(record['source'], 0) + 1
    log.info('Releases feed (published since %s): %s new %s, %s changed; %s in feed, %s open',
             feed_start, len(new), by_source, len(changed), total, open_count or 0)
    return {'new': new, 'changed': changed, 'feed_start': feed_start, 'total': total}


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description='Update the releases feed')
    parser.add_argument('--since', help='Feed start date YYYY-MM-DD (default: kept from the first build)')
    args = parser.parse_args()
    build(args.since)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
