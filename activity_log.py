#!/usr/bin/env python3
"""Audit trail of everything the system scrapes and shares: databases/activity.db.

  runs     - every batch run: when it started/finished and how it went
  scrapes  - per spider per run: new / updated / unchanged counts and timing
  items    - every record that was new or changed, per run (what was scraped)
  shares   - every refresh of a Google Sheet: when, which sheet, rows per tab
  access   - who can open each sheet: first seen, last seen, removed (who it was shared to)
  emails   - every outreach email sent or failed: when, to whom, about what

Recording never breaks a run: any failure here is logged and ignored.

Usage:
  python activity_log.py                 # recent runs, sheet refreshes and who has access
  python activity_log.py --items 50      # the last 50 new/changed records
"""
import argparse
import json
import logging
import os
import socket
import sqlite3
from contextlib import closing
from datetime import datetime

from project_paths import database_directory

log = logging.getLogger('activity_log')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, started_at TEXT, finished_at TEXT, status TEXT,
    host TEXT, spiders TEXT, summary TEXT);
CREATE TABLE IF NOT EXISTS scrapes (
    id INTEGER PRIMARY KEY, run_id TEXT, spider TEXT, finished_at TEXT, status TEXT,
    new INTEGER, updated INTEGER, unchanged INTEGER, skipped_final INTEGER,
    total_after INTEGER, duration_seconds REAL);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY, run_id TEXT, recorded_at TEXT, dataset TEXT, change TEXT,
    reference TEXT, title TEXT, url TEXT);
CREATE INDEX IF NOT EXISTS idx_items_run ON items(run_id);
CREATE INDEX IF NOT EXISTS idx_items_url ON items(url);
CREATE TABLE IF NOT EXISTS shares (
    id INTEGER PRIMARY KEY, run_id TEXT, shared_at TEXT, dataset TEXT, spreadsheet_title TEXT,
    spreadsheet_url TEXT, tabs TEXT, total_rows INTEGER, status TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS access (
    spreadsheet_id TEXT, dataset TEXT, email TEXT, name TEXT, role TEXT, kind TEXT,
    first_seen_at TEXT, last_seen_at TEXT, removed_at TEXT,
    PRIMARY KEY (spreadsheet_id, email));
CREATE TABLE IF NOT EXISTS emails (
    id INTEGER PRIMARY KEY, run_id TEXT, sent_at TEXT, to_email TEXT, subject TEXT,
    release_key TEXT, status TEXT, error TEXT);
'''

# First present field wins: how each spider names its reference, title and link.
REFERENCE_FIELDS = ('CN ID', 'ATM ID', 'SON ID', 'rfq_id', 'reference', 'ID', 'source_id')
TITLE_FIELDS = ('Title', 'title', 'Description', 'Agency')
URL_FIELDS = ('URL', 'url')


def now():
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def run_id():
    """The batch run this process belongs to (set by run_spiders.py), else a manual run."""
    if not os.environ.get('ACTIVITY_RUN_ID'):
        os.environ['ACTIVITY_RUN_ID'] = 'manual-' + datetime.now().strftime('%Y%m%d-%H%M%S')
    return os.environ['ACTIVITY_RUN_ID']


def connect():
    # ACTIVITY_DB lets tests (or a second environment) keep their own log.
    conn = sqlite3.connect(os.environ.get('ACTIVITY_DB') or database_directory() / 'activity.db', timeout=30)
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    return conn


def _safe(fn):
    """Audit logging must never fail the work it is recording."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log.warning('Activity log: %s failed: %s', fn.__name__, e)
    wrapper.__name__ = fn.__name__
    return wrapper


def _first(item, fields):
    for name in fields:
        value = item.get(name)
        if value not in (None, ''):
            return str(value)
    return ''


def item_event(dataset, change, item):
    """(dataset, change, reference, title, url) for one scraped item dict."""
    return (dataset, change, _first(item, REFERENCE_FIELDS), _first(item, TITLE_FIELDS)[:500],
            _first(item, URL_FIELDS))


# ── Writers ───────────────────────────────────────────────────────────────────
@_safe
def start_run(rid, spiders):
    with closing(connect()) as conn:
        conn.execute('INSERT OR REPLACE INTO runs (run_id, started_at, status, host, spiders) '
                     'VALUES (?, ?, ?, ?, ?)', (rid, now(), 'running', socket.gethostname(), ','.join(spiders)))
        conn.commit()


@_safe
def finish_run(rid, status, summary):
    with closing(connect()) as conn:
        conn.execute('UPDATE runs SET finished_at = ?, status = ?, summary = ? WHERE run_id = ?',
                     (now(), status, summary, rid))
        conn.commit()


@_safe
def record_scrape(rid, result):
    with closing(connect()) as conn:
        conn.execute('INSERT INTO scrapes (run_id, spider, finished_at, status, new, updated, unchanged, '
                     'skipped_final, total_after, duration_seconds) VALUES (?,?,?,?,?,?,?,?,?,?)',
                     (rid, result['spider'], now(), result['status'], result.get('new', 0),
                      result.get('updated', 0), result.get('unchanged', 0), result.get('skipped_final', 0),
                      result.get('after'), result.get('duration_seconds')))
        conn.commit()


@_safe
def record_items(events):
    """events: iterable of (dataset, change, reference, title, url)."""
    events = list(events)
    if not events:
        return
    rid, at = run_id(), now()
    with closing(connect()) as conn:
        conn.executemany('INSERT INTO items (run_id, recorded_at, dataset, change, reference, title, url) '
                         'VALUES (?,?,?,?,?,?,?)', [(rid, at, *e) for e in events])
        conn.commit()


@_safe
def record_share(dataset, title, url, tabs=None, status='ok', error=''):
    """tabs: {tab title: row count}."""
    tabs = tabs or {}
    with closing(connect()) as conn:
        conn.execute('INSERT INTO shares (run_id, shared_at, dataset, spreadsheet_title, spreadsheet_url, '
                     'tabs, total_rows, status, error) VALUES (?,?,?,?,?,?,?,?,?)',
                     (run_id(), now(), dataset, title, url, json.dumps(tabs), sum(tabs.values()),
                      status, str(error)[:500]))
        conn.commit()


@_safe
def record_access(dataset, spreadsheet_id, permissions):
    """permissions: Drive permission dicts. Tracks when each person gained and lost access."""
    at = now()
    people = {}
    for p in permissions:
        email = (p.get('emailAddress') or p.get('domain') or p.get('type') or '').lower()
        people[email] = (p.get('displayName', ''), p.get('role', ''), p.get('type', ''))
    with closing(connect()) as conn:
        for email, (name, role, kind) in people.items():
            conn.execute('''INSERT INTO access (spreadsheet_id, dataset, email, name, role, kind,
                                                first_seen_at, last_seen_at, removed_at)
                            VALUES (?,?,?,?,?,?,?,?,NULL)
                            ON CONFLICT(spreadsheet_id, email) DO UPDATE SET
                                name = excluded.name, role = excluded.role, kind = excluded.kind,
                                last_seen_at = excluded.last_seen_at, removed_at = NULL,
                                first_seen_at = CASE WHEN access.removed_at IS NULL
                                                     THEN access.first_seen_at ELSE excluded.first_seen_at END''',
                         (spreadsheet_id, dataset, email, name, role, kind, at, at))
        current = list(people) or ['']
        conn.execute(f'UPDATE access SET removed_at = ? WHERE spreadsheet_id = ? AND removed_at IS NULL '
                     f'AND email NOT IN ({",".join("?" * len(current))})', (at, spreadsheet_id, *current))
        conn.commit()


@_safe
def record_email(to_email, subject, release_key, status, error=''):
    with closing(connect()) as conn:
        conn.execute('INSERT INTO emails (run_id, sent_at, to_email, subject, release_key, status, error) '
                     'VALUES (?,?,?,?,?,?,?)', (run_id(), now(), to_email, subject, release_key, status,
                                                str(error)[:500]))
        conn.commit()


# ── Report ────────────────────────────────────────────────────────────────────
def report(items=0):
    with closing(connect()) as conn:
        def show(title, sql, *params):
            rows = conn.execute(sql, params).fetchall()
            print(f'\n{title}')
            for row in rows:
                print('  ' + ' | '.join('' if v is None else str(v) for v in row))
            if not rows:
                print('  (none yet)')
        show('Recent runs (started, finished, status, run id)',
             'SELECT started_at, finished_at, status, run_id FROM runs ORDER BY started_at DESC LIMIT 10')
        show('Scraped in the latest run (spider, status, new, updated, unchanged, total)',
             'SELECT spider, status, new, updated, unchanged, total_after FROM scrapes '
             'WHERE run_id = (SELECT run_id FROM runs ORDER BY started_at DESC LIMIT 1)')
        show('New/changed records per dataset, all time (dataset, change, count)',
             'SELECT dataset, change, COUNT(*) FROM items GROUP BY dataset, change ORDER BY dataset')
        show('Latest sheet refreshes (time, sheet, rows, status)',
             'SELECT shared_at, spreadsheet_title, total_rows, status FROM shares ORDER BY id DESC LIMIT 9')
        show('Who has access to the sheets (person, role, sheets, since, still has access?)',
             "SELECT email, role, COUNT(*), MIN(first_seen_at), "
             "CASE WHEN SUM(removed_at IS NULL) > 0 THEN 'yes' ELSE 'removed ' || MAX(removed_at) END "
             "FROM access GROUP BY email, role ORDER BY MIN(first_seen_at)")
        show('Emails sent (time, to, status, subject)',
             'SELECT sent_at, to_email, status, subject FROM emails ORDER BY id DESC LIMIT 20')
        if items:
            show(f'Last {items} new/changed records (time, dataset, change, reference, title)',
                 'SELECT recorded_at, dataset, change, reference, substr(title, 1, 70) FROM items '
                 'ORDER BY id DESC LIMIT ?', items)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Show what was scraped and shared, and when')
    parser.add_argument('--items', type=int, default=0, help='Also list the last N new/changed records')
    report(parser.parse_args(argv).items)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
