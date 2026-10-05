#!/usr/bin/env python3
"""Personalised outreach to the contacts on newly released, people-related tenders.

Flow (all state lives in the Google Sheet, so a person can review it):
  1. queue  - new open releases with a contact email that are about people
              (labour hire, staffing, recruitment, nursing...) are added to the
              'outreach' tab with a personalised draft. Nothing is sent.
  2. review - type "yes" in the approve column of the rows to send.
  3. send   - sends approved rows from your mailbox (SMTP), slowly, capped per run.

Safeguards: one email per address per OUTREACH_COOLDOWN_DAYS, a 'do_not_contact'
tab plus local suppression list, contacts who asked for questions via the portal
are never emailed, every email identifies the sender and offers an opt-out.

Usage:
  python outreach.py queue [--from-db] [--dry-run]     # add drafts to the sheet
  python outreach.py send [--dry-run] [--auto-approve] [--max 20]
  python outreach.py suppress someone@agency.gov.au    # never email this address
Settings: OUTREACH_* and SMTP_* in .env (see .env.example).
"""
import argparse
import logging
import os
import re
import smtplib
import sqlite3
import sys
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

from dotenv import load_dotenv

import activity_log
from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('outreach')

TEMPLATE_PATH = PROJECT_DIR / 'templates' / 'outreach_email.txt'
OUTREACH_COLUMNS = ['queued_at', 'approve', 'status', 'sent_at', 'contact_email', 'greeting_name',
                    'buyer', 'title', 'reference', 'source', 'closes_at', 'url', 'subject', 'body',
                    'release_key', 'note']
SOURCE_NAMES = {'austender_atm': 'AusTender', 'buyict': 'BuyICT', 'act': 'Tenders ACT',
                'nsw': 'buy.nsw', 'austender_plan': 'AusTender'}
# Tenders "about people". Matched against title + category, case-insensitive.
DEFAULT_KEYWORDS = [
    'labour hire', 'labor hire', 'recruit', 'staffing', 'personnel', 'workforce', 'temporary staff',
    'contractor resourcing', 'secondment', 'nurs', 'midwi', 'allied health', 'locum', 'clinician',
    'care worker', 'support worker', 'disability support', 'aged care staff',
    '80111600', 'temporary personnel', 'human resources services',
]
SPECIALTIES = [  # first match wins: (keywords in title/category, how we describe ourselves)
    (('nurs', 'midwi', 'clinic', 'health', 'locum', 'care'), 'nursing, allied health and care staff'),
    (('data', 'developer', 'engineer', 'architect', 'analyst', 'ict', 'cyber', 'security', 'cloud',
      'tester', 'designer', 'digital', 'service desk', 'software'), 'ICT specialists'),
]
TITLES = {'mr', 'mrs', 'ms', 'miss', 'mx', 'dr', 'prof', 'professor', 'sir', 'dame'}
NOT_A_PERSON = re.compile(r'no contact|question|portal|procurement|tender|team|unit|branch|@', re.I)


def env(name, default=''):
    return os.environ.get(name, default).strip().strip('"').strip("'")


# ── Selection ──────────────────────────────────────────────────────────────────
def keywords():
    custom = env('OUTREACH_KEYWORDS')
    return [k.strip().lower() for k in custom.split(',') if k.strip()] if custom else DEFAULT_KEYWORDS


def is_people_tender(row):
    if row.get('source') == 'buyict' and str(row.get('source_id', '')).upper().startswith('LH-'):
        return True  # BuyICT labour-hire requests are always for people
    text = f"{row.get('title', '')} {row.get('category', '')}".lower()
    return any(k in text for k in keywords())


def release_key(row):
    return f"{row.get('source')}:{row.get('url')}"


def select_candidates(releases, already_queued, suppressed, today=None):
    """Open, people-related releases with a usable email that are not yet queued."""
    today = today or date.today()
    chosen, seen_keys = [], set(already_queued)
    for row in releases:
        email = (row.get('contact_email') or '').strip().lower()
        key = release_key(row)
        if not email or key in seen_keys or email in suppressed:
            continue
        if (row.get('status') or '').lower() != 'open' or not is_people_tender(row):
            continue
        closes = row.get('closes_on') or ''
        if closes and closes <= today.isoformat():
            continue  # closes today or already closed: too late to be useful
        seen_keys.add(key)
        chosen.append(row)
    return chosen


# ── Personalisation ────────────────────────────────────────────────────────────
def greeting_name(contact_name):
    name = (contact_name or '').strip()
    words = [w for w in name.replace('.', ' ').split() if w.lower() not in TITLES]
    if not words or NOT_A_PERSON.search(name) or len(words) > 4:
        return 'there'
    return words[0].title()


def role_parts(title):
    """BuyICT titles lead with a headcount: '7 Content Designers' -> ('Content Designers', 'Content Designers'),
    '1 Senior Analyst' -> ('Senior Analyst', 'a Senior Analyst'). Other titles -> (title, None)."""
    match = re.match(r'^\s*(\d+)\s+(.+)$', title or '')
    if not match:
        return (title or '').strip(), None
    count, role = int(match.group(1)), match.group(2).strip()
    if count > 1:
        return role, role
    return role, f"{'an' if role[:1].lower() in 'aeiou' else 'a'} {role}"


def specialty(row):
    text = f"{row.get('title', '')} {row.get('category', '')}".lower()
    for words, label in SPECIALTIES:
        if any(w in text for w in words):
            return label
    return env('OUTREACH_SPECIALTY', 'skilled staff')


def _friendly_day(iso_day):
    """'2026-10-11' -> 'Sunday 11 October 2026'."""
    try:
        day = date.fromisoformat(str(iso_day))
    except ValueError:
        return ''
    return f"{day.strftime('%A')} {day.day} {day.strftime('%B %Y')}"


def render(row, template=None):
    """Return (subject, body) for one release."""
    template = template or TEMPLATE_PATH.read_text(encoding='utf-8')
    role_title, role_phrase = role_parts(row.get('title', ''))
    abn = env('OUTREACH_ABN')
    values = {
        'greeting_name': greeting_name(row.get('contact_name')),
        'buyer': row.get('buyer') or 'your agency',
        'title': row.get('title', ''),
        'role_title': role_title,
        'role_phrase': role_phrase or specialty(row),
        'reference': row.get('source_id') or 'ref. n/a',
        'source_name': SOURCE_NAMES.get(row.get('source'), 'the tender portal'),
        'closes': _friendly_day(row.get('closes_on')) or row.get('closes_at') or 'soon',
        'specialty': specialty(row),
        'company': env('OUTREACH_COMPANY', 'Illoura Services'),
        'abn_line': f' | ABN {abn}' if abn else '',
        'from_name': env('OUTREACH_FROM_NAME'),
        'from_email': env('OUTREACH_FROM_EMAIL'),
        'phone': env('OUTREACH_PHONE'),
        'address': env('OUTREACH_ADDRESS'),
    }
    text = template.format_map(values)
    first, _, body = text.partition('\n')
    subject = first.replace('Subject:', '', 1).strip()
    return subject, body.strip() + '\n'


# ── Local log: cooldown and suppression survive sheet edits ────────────────────
def local_db():
    conn = sqlite3.connect(database_directory() / 'outreach.db')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS sent (id INTEGER PRIMARY KEY, email TEXT, release_key TEXT,
            subject TEXT, sent_at TEXT, status TEXT, error TEXT);
        CREATE INDEX IF NOT EXISTS idx_sent_email ON sent(email, sent_at);
        CREATE TABLE IF NOT EXISTS suppressed (email TEXT PRIMARY KEY, reason TEXT, added_at TEXT);
    ''')
    return conn


def recently_emailed(conn, email, days):
    cutoff = (datetime.now() - timedelta(days=days)).isoformat(sep=' ')
    return conn.execute("SELECT 1 FROM sent WHERE email = ? AND status = 'sent' AND sent_at >= ?",
                        (email, cutoff)).fetchone() is not None


# ── Google Sheet store ─────────────────────────────────────────────────────────
class SheetStore:
    """Releases come from the 'Tender Feed - New Releases' spreadsheet; drafts and the
    do-not-contact list live in 'Tender Feed - Outreach' (see publish_sheets.py).
    Before those exist, everything falls back to tabs of the original single spreadsheet."""

    def __init__(self):
        import publish_sheets
        import settings as project_settings
        gc = publish_sheets.client()
        registry = publish_sheets.load_registry()
        if registry.get('releases') and registry.get('outreach'):
            self.releases_book = gc.open_by_key(registry['releases'])
            self.releases_tab = 'releases'
            self.book = gc.open_by_key(registry['outreach'])
        else:
            self.book = self.releases_book = gc.open_by_key(project_settings.GSHEETS_SPREADSHEET_ID)
            self.releases_tab = 'new_releases'

    def _tab(self, title, header):
        import gspread
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title=title, rows=200, cols=len(header))
            ws.append_row(header, value_input_option='RAW')
            return ws

    def releases(self):
        return self.releases_book.worksheet(self.releases_tab).get_all_records()

    def outreach_rows(self):
        return self._tab('outreach', OUTREACH_COLUMNS).get_all_records(expected_headers=OUTREACH_COLUMNS)

    def append_outreach(self, rows):
        self._tab('outreach', OUTREACH_COLUMNS).append_rows(
            [[r.get(c, '') for c in OUTREACH_COLUMNS] for r in rows], value_input_option='RAW')

    def update_outreach(self, key, **fields):
        """Update the row whose release_key is `key`. Rows are found by key at write time,
        so people may sort, filter or delete rows in the sheet while a send is running."""
        ws = self._tab('outreach', OUTREACH_COLUMNS)
        header = ws.row_values(1)
        keys = ws.col_values(header.index('release_key') + 1)
        if key not in keys:
            log.warning('Outreach row %s is no longer in the sheet; status not recorded there', key)
            return
        row = keys.index(key) + 1
        for name, value in fields.items():
            ws.update_cell(row, header.index(name) + 1, value)

    def suppressed(self):
        ws = self._tab('do_not_contact', ['email', 'reason', 'added_at'])
        return {str(r.get('email', '')).strip().lower() for r in ws.get_all_records() if r.get('email')}

    def add_suppressed(self, email, reason):
        self._tab('do_not_contact', ['email', 'reason', 'added_at']).append_row(
            [email, reason, datetime.now().isoformat(sep=' ', timespec='seconds')], value_input_option='RAW')


def releases_from_db():
    path = database_directory() / 'releases.db'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute('SELECT * FROM releases ORDER BY first_seen_at DESC')]


# ── Commands ───────────────────────────────────────────────────────────────────
def all_suppressed(store, conn):
    return store.suppressed() | {r[0] for r in conn.execute('SELECT email FROM suppressed')}


def queue(store, from_db=False, dry_run=False):
    with closing(local_db()) as conn:
        suppressed = all_suppressed(store, conn)
    releases = releases_from_db() if from_db else store.releases()
    queued_keys = {r.get('release_key') for r in store.outreach_rows()}
    candidates = select_candidates(releases, queued_keys, suppressed)
    now = datetime.now().isoformat(sep=' ', timespec='seconds')
    rows = []
    for row in candidates:
        subject, body = render(row)
        rows.append({'queued_at': now, 'approve': '', 'status': 'queued', 'sent_at': '',
                     'contact_email': row['contact_email'].strip(),
                     'greeting_name': greeting_name(row.get('contact_name')),
                     'buyer': row.get('buyer', ''), 'title': row.get('title', ''),
                     'reference': row.get('source_id', ''), 'source': row.get('source', ''),
                     'closes_at': row.get('closes_at', ''), 'url': row.get('url', ''),
                     'subject': subject, 'body': body, 'release_key': release_key(row), 'note': ''})
    if dry_run:
        for r in rows:
            print(f"--- To: {r['contact_email']}\nSubject: {r['subject']}\n\n{r['body']}")
    elif rows:
        store.append_outreach(rows)
    log.info('Outreach: %s of %s releases matched; %s', len(rows), len(releases),
             'previewed (dry run)' if dry_run else "added to the 'outreach' tab for approval")
    return rows


def smtp_connection():
    host, port = env('SMTP_HOST'), int(env('SMTP_PORT', '587'))
    if not host:
        raise RuntimeError('SMTP_HOST is not set in .env')
    server = smtplib.SMTP_SSL(host, port, timeout=60) if port == 465 else smtplib.SMTP(host, port, timeout=60)
    if port != 465:
        server.starttls()
    if env('SMTP_USER'):
        server.login(env('SMTP_USER'), env('SMTP_PASSWORD'))
    return server


def build_message(row):
    sender = env('OUTREACH_FROM_EMAIL')
    if not sender:
        raise RuntimeError('OUTREACH_FROM_EMAIL is not set in .env')
    msg = EmailMessage()
    msg['From'] = formataddr((env('OUTREACH_FROM_NAME'), sender))
    msg['To'] = row['contact_email']
    msg['Reply-To'] = env('OUTREACH_REPLY_TO') or sender
    msg['Subject'] = row['subject']
    msg['Message-ID'] = make_msgid(domain=sender.split('@')[-1])
    msg['List-Unsubscribe'] = f'<mailto:{sender}?subject=unsubscribe>'
    msg.set_content(row['body'])
    return msg


def send(store, dry_run=False, auto_approve=False, max_emails=None, delay=None, smtp_factory=smtp_connection):
    max_emails = max_emails if max_emails is not None else int(env('OUTREACH_MAX_PER_RUN', '20'))
    delay = delay if delay is not None else float(env('OUTREACH_DELAY_SECONDS', '30'))
    cooldown = int(env('OUTREACH_COOLDOWN_DAYS', '14'))
    sent = skipped = 0
    with closing(local_db()) as conn:
        suppressed = all_suppressed(store, conn)
        # 1. Decide, without touching the mailbox.
        to_send, planned = [], set()
        for row in store.outreach_rows():
            if row.get('status') != 'queued':
                continue
            approved = str(row.get('approve', '')).strip().lower() in ('yes', 'y', 'true', '1', 'x')
            if not (approved or auto_approve):
                continue
            email = str(row.get('contact_email', '')).strip().lower()
            reason = ('on do-not-contact list' if email in suppressed else
                      f'emailed in the last {cooldown} days'
                      if email in planned or recently_emailed(conn, email, cooldown) else
                      'tender closed' if row.get('closes_at') and _closed(row['closes_at']) else None)
            if reason:
                skipped += 1
                if not dry_run:
                    store.update_outreach(row['release_key'], status='skipped', note=reason)
                log.info('Skip %s: %s', email, reason)
            elif len(to_send) < max_emails:
                planned.add(email)
                to_send.append((email, row))
        if len(to_send) == max_emails:
            log.info('Per-run limit of %s emails reached; any further approved drafts stay queued', max_emails)
        if dry_run:
            for email, row in to_send:
                log.info('Would send to %s: %s', email, row.get('subject'))
            log.info('Outreach send: %s would be sent (dry run), %s skipped', len(to_send), skipped)
            return len(to_send)
        if not to_send:
            log.info('Outreach send: nothing approved to send, %s skipped', skipped)
            return 0
        # 2. Connect first: a login failure leaves every draft queued, not failed.
        server = smtp_factory()
        try:
            for email, row in to_send:
                if sent:
                    time.sleep(delay)  # pace sends: looks like a person, protects the mailbox
                now = datetime.now().isoformat(sep=' ', timespec='seconds')
                try:
                    server.send_message(build_message(row))
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
                    # This address (or message) is rejected; others can still go.
                    conn.execute('INSERT INTO sent (email, release_key, subject, sent_at, status, error) '
                                 'VALUES (?,?,?,?,?,?)', (email, row['release_key'], row.get('subject'),
                                                          now, 'failed', str(e)))
                    conn.commit()
                    store.update_outreach(row['release_key'], status='failed', note=str(e)[:200])
                    activity_log.record_email(email, row.get('subject'), row['release_key'], 'failed', e)
                    log.error('Rejected %s: %s', email, e)
                    continue
                except Exception as e:
                    # Connection-level problem: stop; this and later drafts stay queued for next time.
                    log.error('Sending stopped at %s (%s); remaining drafts stay queued', email, e)
                    break
                conn.execute('INSERT INTO sent (email, release_key, subject, sent_at, status) VALUES (?,?,?,?,?)',
                             (email, row['release_key'], row.get('subject'), now, 'sent'))
                conn.commit()
                sent += 1
                activity_log.record_email(email, row.get('subject'), row['release_key'], 'sent')
                store.update_outreach(row['release_key'], status='sent', sent_at=now)
                log.info('Sent to %s: %s', email, row.get('subject'))
        finally:
            try:
                server.quit()
            except Exception:
                pass
    log.info('Outreach send: %s sent, %s skipped', sent, skipped)
    return sent


def _closed(closes_at):
    from build_releases import parse_day
    day = parse_day(closes_at)
    return day is not None and day <= date.today()


def suppress(store, email, reason):
    email = email.strip().lower()
    with closing(local_db()) as conn:
        conn.execute('INSERT OR REPLACE INTO suppressed VALUES (?, ?, ?)',
                     (email, reason, datetime.now().isoformat(sep=' ', timespec='seconds')))
        conn.commit()
    store.add_suppressed(email, reason)
    log.info('%s added to do-not-contact', email)


def main(argv=None):
    load_dotenv(PROJECT_DIR / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    parser = argparse.ArgumentParser(description='Personalised outreach for new people-related tenders')
    sub = parser.add_subparsers(dest='command', required=True)
    q = sub.add_parser('queue', help="Add drafts for new matching releases to the 'outreach' tab")
    q.add_argument('--from-db', action='store_true', help='Read releases from releases.db instead of the sheet')
    q.add_argument('--dry-run', action='store_true', help='Print the drafts; change nothing')
    s = sub.add_parser('send', help='Send approved drafts')
    s.add_argument('--dry-run', action='store_true', help='Show what would be sent; send nothing')
    s.add_argument('--auto-approve', action='store_true', help='Send every queued draft without a "yes"')
    s.add_argument('--max', type=int, help='Maximum emails this run (default OUTREACH_MAX_PER_RUN or 20)')
    d = sub.add_parser('suppress', help='Never email this address again')
    d.add_argument('email')
    d.add_argument('--reason', default='requested')
    args = parser.parse_args(argv)
    store = SheetStore()
    if args.command == 'queue':
        queue(store, from_db=args.from_db, dry_run=args.dry_run)
    elif args.command == 'send':
        send(store, dry_run=args.dry_run, auto_approve=args.auto_approve, max_emails=args.max)
    else:
        suppress(store, args.email, args.reason)
    return 0


if __name__ == '__main__':
    sys.exit(main())
