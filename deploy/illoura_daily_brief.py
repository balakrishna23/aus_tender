"""Scheduled daily brief: collect, select, verify, compose, check, then send once at 08:15 Sydney.

Requested by Sirish on 12 November 2026: run the collection and send the internal brief
daily at 08:15 Australia/Sydney. The recipient is the internal mailbox fixed in the
server's private send helper; this never contacts buyers or partners.

Subcommands (installed in /opt/illoura-ops; the timer runs `run` as root):
  run                 collect + publish (root), compose (scraper), wait, send (scraper)
  compose --out RUN   select and verify listings, write editorial notes, compose, layout check
  send --out RUN      send once through the existing send helper and record the receipt
  reconcile --out RUN [--mark-undelivered]   check an uncertain send in Gmail Sent; never send
  dry-run             compose a copy of the latest run under /var/tmp; never sends

Selection is automatic and the email says so: open, relevant BuyICT ICT labour-hire
requests closing soonest (at least 18 hours away), each rechecked on the public listing
this morning; requests that fail the live check are dropped, not guessed.
"""
import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT = Path('/opt/aus_tender')
WORK = Path('/opt/illoura-routine')
SCRIPTS = WORK / 'scripts'
OPS = Path('/opt/illoura-ops')
PYTHON = PROJECT / '.venv/bin/python'
TOKEN = Path('/etc/illoura-routine/gmail-token.json')
SYDNEY = ZoneInfo('Australia/Sydney')
INDIA = ZoneInfo('Asia/Kolkata')
SEND_AT = (8, 15)            # local Sydney time
LATEST_SEND = (12, 0)        # after this, skip the day instead of sending a stale brief
MAX_SELECTED = 8
MIN_HOURS_AHEAD = 18
AUTHORIZATION = 'User requested the Illoura routine and email on AWS'
SCHEDULE_NOTE = 'Daily 08:15 Australia/Sydney, requested by Sirish on 12 November 2026'
RESTRICTED = re.compile(r'selected sellers?|invited sellers?|by invitation|invitation only|only (?:be )?open to|'
                        r'additional sellers will not|existing panel members only', re.I)
SEND_ARTEFACTS = ('send_receipt.json', 'send_attempt.json', 'send_reconciliation.json', 'cloud_send_review.json', 'mail_authorization.json',
                  'outbound_send_request.json', 'newsletter_editorial.json', 'editorial_notes.json',
                  'email_layout_checks.json')


def utc_now():
    return datetime.now(timezone.utc)


def write_json(path, value):
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


# --------------------------------------------------------------------------- selection

CANBERRA = ZoneInfo('Australia/Canberra')


def _open_buyict(intelligence_db):
    """(closing datetime, id, title, buyer, url) for open relevant BuyICT requests with a valid close time.

    closes_at carries its own UTC offset, so it is parsed rather than compared as text.
    """
    with closing(sqlite3.connect(Path(intelligence_db).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        rows = db.execute("SELECT source_id, closes_at, title, buyer, url FROM opportunities "
                          "WHERE source='BuyICT' AND status_effective='Open' AND is_relevant=1").fetchall()
    parsed = []
    for key, closes, title, buyer, url in rows:
        try:
            end = datetime.fromisoformat(closes or '')
        except ValueError:
            continue
        if end.tzinfo is not None:
            parsed.append((end, key, title, buyer, url))
    return sorted(parsed, key=lambda row: (row[0], row[1]))


def select_ids(intelligence_db, now, limit=MAX_SELECTED, min_hours=MIN_HOURS_AHEAD):
    """Open, relevant BuyICT requests closing soonest; labour hire first, then others to fill."""
    earliest, latest = now + timedelta(hours=min_hours), now + timedelta(days=21)
    window = [row[1] for row in _open_buyict(intelligence_db) if earliest < row[0] <= latest]
    chosen = [key for key in window if key.startswith('LH-')][:limit]
    if len(chosen) < 3:
        chosen += [key for key in window if key.startswith('PCS-')][:limit - len(chosen)]
    return chosen


def new_since_baseline(intelligence_db, baseline_ids, limit=10):
    """Open relevant requests absent from the pre-run baseline, closing soonest first."""
    known = set(baseline_ids)
    rows = [row for row in _open_buyict(intelligence_db) if row[1] not in known][:limit]
    return [(key, title, buyer, end.astimezone(CANBERRA).strftime('%d %B, %I:%M %p Canberra'), url)
            for end, key, title, buyer, url in rows]


def clean(text):
    """The layout check rejects U+FFFD; source text with a damaged character must not block the brief."""
    return str(text or '').replace('�', '-').strip()


def restricted_hint(record):
    content = record.get('content', {})
    text = json.dumps({k: content.get(k) for k in ('description', 'criteriaText', 'additionalRequirements',
                                                   'response_requirements')}, ensure_ascii=False)
    return bool(RESTRICTED.search(text))


def still_open(record, now, closing_time):
    data = record.get('content', {})
    details = {x.get('label'): x.get('value') for x in data.get('mainDetails', [])}
    end = closing_time(details.get('RFQ closing date') or details.get('RFI closing date'))
    return bool(end and end > now + timedelta(hours=2) and data.get('rfqOpen') and not data.get('is_cancelled'))


def editorial_notes(edition_day, verified, new_rows, report):
    """Templated, factual notes; compose adds closing time, location, clearance and Q&A details."""
    date_text = edition_day.strftime('%d %B %Y')
    count = len(verified)
    opportunities = {}
    for key, record in verified.items():
        notes = []  # the introduction already says selection is automatic
        if restricted_hint(record):
            notes.append('The listing text suggests responses are limited to invited or selected sellers; '
                         'check existing access before preparing a response.')
        opportunities[key] = notes
    sections = []
    if new_rows:
        sections.append({'title': 'New on BuyICT since the last run', 'items': [
            {'heading': clean(f'{buyer} | {title} | {key}'), 'paragraphs': [f'Closes {closes}.'],
             'links': [{'label': key + ' official listing', 'url': url}]}
            for key, title, buyer, closes, url in new_rows]})
    gaps = [x['spider'] for x in report.get('results', []) if x.get('status') != 'OK']
    return {
        'edition_date': edition_day.isoformat(),
        'subject': f'Illoura daily brief | {date_text} | {count} open request{"s" if count != 1 else ""} to check',
        'preheader': f'Automated 8:15 brief: {count} open BuyICT requests closing soonest, rechecked this morning.',
        'introduction': ('This is the automated daily brief. It lists the open ICT labour-hire requests '
                         'closing soonest, each rechecked on its public BuyICT listing this morning. Check existing '
                         'seller access and candidate availability before committing response effort.'),
        'opportunities': opportunities,
        'additional_sections': sections,
        'coverage_note': ('Selection was automatic (no human editorial review); listings that failed the live '
                          'check were left out.' + (' Source gaps: ' + ', '.join(gaps) + '.' if gaps else '')),
    }


# --------------------------------------------------------------------------- phases

def scraper(*args, env=None):
    command = ['runuser', '-u', 'scraper', '--', str(PYTHON), *args]
    subprocess.run(command, check=True, cwd=str(WORK), env=env)


def compose(out, now=None):
    """Runs as scraper inside the run directory."""
    sys.path[:0] = [str(PROJECT), str(SCRIPTS)]
    from classify import closing_time  # noqa: E402  (server project module)
    out = Path(out).resolve()
    if (out / 'send_receipt.json').exists():
        raise SystemExit('This edition already has a send receipt')
    now = now or utc_now()
    edition_day = now.astimezone(INDIA).date()  # the composer's edition date is the India date
    intelligence = PROJECT / 'databases/intelligence.db'
    shortlist = out / 'shortlist'
    shortlist.mkdir(exist_ok=True)
    verified, dropped = {}, []
    for key in select_ids(intelligence, now):
        result = subprocess.run([str(PYTHON), str(SCRIPTS / 'refresh_daily_shortlist.py'), '--out', str(shortlist),
                                 '--ids', key], cwd=str(WORK), capture_output=True, text=True)
        path = shortlist / (key + '.json')
        if result.returncode or not path.exists():
            dropped.append({'id': key, 'reason': 'live check failed'})
            continue
        record = json.loads(path.read_text(encoding='utf-8'))
        if still_open(record, now, closing_time):
            verified[key] = record
        else:
            dropped.append({'id': key, 'reason': 'no longer open or closing within two hours'})
    checked = len(verified) + len(dropped)
    failed = sum(item['reason'] == 'live check failed' for item in dropped)
    if checked and failed * 2 > checked:
        raise SystemExit('More than half of shortlisted live checks failed; not approving the send')
    baseline_file = out / 'baseline_buyict_ids.json'
    baseline = json.loads(baseline_file.read_text()) if baseline_file.exists() else []
    report = json.loads((out / 'source_refresh.json').read_text(encoding='utf-8'))
    notes = editorial_notes(edition_day, verified, new_since_baseline(intelligence, baseline), report)
    write_json(out / 'editorial_notes.json', notes)
    if not (out / 'newsletter_review.json').exists():
        write_json(out / 'newsletter_review.json', {'created_by': 'illoura_daily_brief'})
    subprocess.run([str(PYTHON), str(SCRIPTS / 'compose_checked_brief.py'), '--out', str(out)],
                   check=True, cwd=str(WORK))
    env = dict(os.environ, PLAYWRIGHT_BROWSERS_PATH=str(PROJECT / '.browsers'))
    subprocess.run([str(PYTHON), str(SCRIPTS / 'check_newsletter_layout.py'), '--out', str(out),
                    '--edition-date', edition_day.strftime('%d %B %Y')], check=True, cwd=str(WORK), env=env)
    checks = json.loads((out / 'email_layout_checks.json').read_text())
    if len(checks) != 2 or not all(c.get('passed') for c in checks):
        raise SystemExit('Layout checks failed; not approving the send')
    import hashlib
    digests = {n: hashlib.sha256((out / n).read_bytes()).hexdigest()
               for n in ('newsletter_preview.html', 'newsletter_preview.txt', 'newsletter_editorial.json')}
    write_json(out / 'cloud_send_review.json', {
        'authorization': AUTHORIZATION, 'reviewed': True, 'review_type': 'automated checks',
        'schedule': SCHEDULE_NOTE, 'reviewed_at_utc': utc_now().isoformat(), 'sha256': digests,
        'selected': sorted(verified), 'dropped': dropped, 'layout_checks_passed': True})
    summary = {'run_dir': str(out), 'subject': notes['subject'], 'selected': sorted(verified), 'dropped': dropped,
               'new_since_last_run': len(notes['additional_sections'][0]['items']) if notes['additional_sections'] else 0}
    print(json.dumps(summary))
    return summary


def send_window(now):
    local = now.astimezone(SYDNEY)
    target = local.replace(hour=SEND_AT[0], minute=SEND_AT[1], second=0, microsecond=0)
    cutoff = local.replace(hour=LATEST_SEND[0], minute=LATEST_SEND[1], second=0, microsecond=0)
    return target, cutoff


@contextmanager
def send_lock():
    import fcntl
    with (WORK / 'send.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit('Another send or reconciliation is in progress')
        yield


def check_edition(out):
    edition = json.loads((out / 'editorial_notes.json').read_text())['edition_date']
    datetime.strptime(edition, '%Y-%m-%d')
    for other in (WORK / 'reports').iterdir():
        if other.resolve() == out or not other.is_dir():
            continue
        notes = other / 'editorial_notes.json'
        if not notes.exists() or json.loads(notes.read_text()).get('edition_date') != edition:
            continue
        receipt = other / 'send_receipt.json'
        if receipt.exists() and json.loads(receipt.read_text()).get('sent') is True:
            raise SystemExit('This edition was already sent from another run')
        reconciliation = other / 'send_reconciliation.json'
        resolved = (reconciliation.exists()
                    and json.loads(reconciliation.read_text()).get('delivered') is False)
        if (other / 'send_attempt.json').exists() and not receipt.exists() and not resolved:
            raise SystemExit('This edition has an unresolved send attempt in another run; reconcile first')


def record_sent(out, receipt):
    for name in ('cloud_run.json', 'newsletter_review.json'):
        path = out / name
        if path.exists():
            value = json.loads(path.read_text())
            value.update(status='sent', sent=True, sent_at_utc=receipt.get('recorded_at_utc'),
                         send_receipt=str(out / 'send_receipt.json'), schedule=SCHEDULE_NOTE)
            write_json(path, value)
    latest = WORK / 'latest_run.json'
    if latest.exists():
        state = json.loads(latest.read_text())
        if state.get('run_dir') == str(out):
            state.update(status='sent', sent=True, sent_at_utc=receipt.get('recorded_at_utc'))
            write_json(latest, state)


def send(out):
    with send_lock():
        _send_locked(Path(out).resolve())


def _send_locked(out):
    """Runs as scraper. The helper refuses duplicates and checks identity, layout and content hashes."""
    sys.path[:0] = [str(PROJECT), str(SCRIPTS)]
    from send_gmail import send as gmail_send  # noqa: E402  (server routine module)
    out = Path(out).resolve()
    check_edition(out)
    now = utc_now()
    write_json(out / 'mail_authorization.json', {'status': 'authorized', 'schedule': SCHEDULE_NOTE,
                                                 'gmail_credential_installed': TOKEN.exists(),
                                                 'recorded_at_utc': now.isoformat()})
    gmail_send(out, TOKEN)
    receipt = json.loads((out / 'send_receipt.json').read_text())
    record_sent(out, receipt)
    print(json.dumps({'sent': True, 'subject': receipt.get('subject'), 'message_id': receipt.get('message_id')}))


def gmail_session():
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import AuthorizedSession, Request
    credentials = Credentials.from_authorized_user_file(str(TOKEN))
    if not credentials.valid:
        credentials.refresh(Request())
    return AuthorizedSession(credentials)


def find_sent(session, attempt):
    api = 'https://gmail.googleapis.com/gmail/v1/users/me'

    def get(path, **params):
        response = session.get(api + path, params=params, timeout=45)
        response.raise_for_status()
        return response.json()

    sender, recipient, subject = (attempt[key] for key in ('sender', 'recipient', 'subject'))
    if get('/profile')['emailAddress'].lower() != sender.lower():
        raise ValueError('Gmail identity mismatch')
    queries = ['in:sent rfc822msgid:' + attempt['message_id_header'].strip('<>'),
               'in:sent from:' + sender + ' to:' + recipient + ' subject:"' + subject.replace('"', '') + '"']
    searched = []
    for query in queries:
        searched.append(query)
        page = None
        while True:
            params = {'q': query, 'maxResults': 100}
            if page:
                params['pageToken'] = page
            matches = get('/messages', **params)
            for item in matches.get('messages', []):
                message = get('/messages/' + item['id'], format='metadata')
                headers = {h['name'].lower(): h['value'] for h in message.get('payload', {}).get('headers', [])}
                if ('SENT' in message.get('labelIds', []) and headers.get('subject') == subject
                        and recipient in headers.get('to', '') and sender in headers.get('from', '')):
                    return message, searched
            page = matches.get('nextPageToken')
            if not page:
                break
    return None, searched


def reconcile(out, mark_undelivered=False, session=None):
    original = Path(out).absolute()
    out = original.resolve()
    if (original.is_symlink() or out.parent != (WORK / 'reports').resolve()
            or not out.name.startswith('daily_sweep_') or not out.is_dir()):
        raise SystemExit('Unexpected reconciliation run directory')
    with send_lock():
        if (out / 'send_receipt.json').exists():
            print(json.dumps({'status': 'already_sent'}))
            return 0
        if not (out / 'send_attempt.json').exists():
            print(json.dumps({'status': 'nothing_to_reconcile'}))
            return 0
        attempt = json.loads((out / 'send_attempt.json').read_text())
        owned_session = session is None
        session = gmail_session() if owned_session else session
        try:
            message, searched = find_sent(session, attempt)
        finally:
            if owned_session:
                session.close()
        if message:
            now = utc_now()
            sent_at = (datetime.fromtimestamp(int(message['internalDate']) / 1000, timezone.utc)
                       if 'internalDate' in message else now)
            receipt = {'sent': True, 'reconciled_existing_sent': True, 'reconciled_by': 'reconcile',
                       'message_id': message['id'], 'thread_id': message.get('threadId'),
                       'label_ids': message.get('labelIds', []), 'recorded_at_utc': sent_at.isoformat(),
                       'reconciled_at_utc': now.isoformat(),
                       'sender': attempt['sender'], 'recipient': attempt['recipient'], 'subject': attempt['subject']}
            write_json(out / 'send_receipt.json', receipt)
            record_sent(out, receipt)
            print(json.dumps({'status': 'delivered', 'message_id': receipt['message_id'],
                              'thread_id': receipt['thread_id']}))
            return 0
        if not mark_undelivered:
            print(json.dumps({'status': 'not_found_in_sent'}))
            return 2
        write_json(out / 'send_reconciliation.json', {
            'delivered': False, 'checked_at_utc': utc_now().isoformat(),
            'message_id_header': attempt['message_id_header'], 'searched': searched, 'decided_by': 'operator'})
        print(json.dumps({'status': 'marked_undelivered'}))
        return 0


def timer_enabled(systemctl=None):
    try:
        result = (systemctl or subprocess.run)(['systemctl', 'is-enabled', 'illoura-daily-brief.timer'],
                                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() == 'enabled'


def run(systemctl=None):
    """Root, from the timer: collect + publish, compose as scraper, wait for 08:15, send as scraper."""
    subprocess.run([str(PYTHON), str(OPS / 'illoura_cloud_collect.py')], check=True, cwd=str(PROJECT))
    out = json.loads((WORK / 'latest_run.json').read_text())['run_dir']
    scraper(str(OPS / 'illoura_daily_brief.py'), 'compose', '--out', out)
    target, cutoff = send_window(utc_now())
    now = utc_now()
    if now >= cutoff:
        print(json.dumps({'sent': False, 'reason': 'past the 12:00 Sydney cutoff; brief kept for review'}))
        return
    if now < target:
        time.sleep((target - now).total_seconds())
    edition = json.loads((Path(out) / 'editorial_notes.json').read_text())['edition_date']
    edition_day = datetime.strptime(edition, '%Y-%m-%d').date()
    cutoff = datetime.combine(edition_day, datetime.min.time(), tzinfo=SYDNEY).replace(
        hour=LATEST_SEND[0], minute=LATEST_SEND[1])
    if not timer_enabled(systemctl):
        print(json.dumps({'sent': False, 'reason': 'daily brief timer is not enabled; brief kept for review'}))
        return
    now = utc_now()
    if now.astimezone(SYDNEY).date() != edition_day or now >= cutoff:
        print(json.dumps({'sent': False, 'reason': 'past the edition 12:00 Sydney cutoff; brief kept for review'}))
        return
    scraper(str(OPS / 'illoura_daily_brief.py'), 'send', '--out', out)


def dry_run():
    """Root: copy the latest run, compose it as scraper, never send. Leaves the copy for inspection."""
    source = Path(json.loads((WORK / 'latest_run.json').read_text())['run_dir'])
    target = Path('/var/tmp') / ('illoura-brief-dryrun-' + utc_now().strftime('%Y%m%dT%H%M%SZ'))
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(*SEND_ARTEFACTS, 'shortlist', '*.png'))
    subprocess.run(['chown', '-R', 'scraper:scraper', str(target)], check=True)
    scraper(str(OPS / 'illoura_daily_brief.py'), 'compose', '--out', str(target))
    print(json.dumps({'dry_run_dir': str(target), 'sent': False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('run')
    sub.add_parser('dry-run')
    for name in ('compose', 'send'):
        sub.add_parser(name).add_argument('--out', required=True)
    reconcile_parser = sub.add_parser('reconcile')
    reconcile_parser.add_argument('--out', required=True)
    reconcile_parser.add_argument('--mark-undelivered', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'run':
        run()
    elif args.action == 'dry-run':
        dry_run()
    elif args.action == 'compose':
        compose(args.out)
    elif args.action == 'reconcile':
        raise SystemExit(reconcile(args.out, args.mark_undelivered))
    else:
        send(args.out)


if __name__ == '__main__':
    main()
