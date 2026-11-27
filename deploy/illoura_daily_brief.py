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

Selection uses the newest open, relevant BuyICT requests (at least 18 hours away),
with requests absent from the pre-run baseline first, each rechecked on the public listing
this morning; requests that fail the live check are dropped, not guessed.
"""
import argparse
import json
import os
import re
import shutil
import signal
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
LATEST_RETRY_START = (9, 30)  # A full collect retry must still fit before noon.
RETRY_PAUSE_SECONDS = 300  # Give transient upstream failures five minutes to recover.
# Pauses before each further attempt. Collection gets four retries with growing pauses so an
# upstream outage of up to about three hours (BuyICT timed out for 90 minutes on 6 Nov 2026)
# does not cost the day; the 09:30 latest start and the noon cutoff still bound every retry.
RETRY_PAUSES = {'collect': (RETRY_PAUSE_SECONDS, 1200, 2400, 3600), 'compose': (RETRY_PAUSE_SECONDS,)}
COLLECT_TIMEOUT_SECONDS = 7200  # Include the 105-minute routine and publication overhead.
COMPOSE_TIMEOUT_SECONDS = 900  # Bound live checks, composition and browser layout checks.
SEND_MARGIN_SECONDS = 900  # Leave 15 minutes before noon for the single send attempt.
# Optional morning research step (25 Nov 2026: raised from 210 s; the newer research build needs about 400 s).
RESEARCH_CAP_SECONDS = 900
# The research step must also end early enough that the full compose bound plus this reserve fits before 08:15.
SEND_ON_TIME_RESERVE_SECONDS = 300
MAX_SELECTED = 10
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


def select_ids(intelligence_db, now, limit=MAX_SELECTED, min_hours=MIN_HOURS_AHEAD, baseline_ids=()):
    """Newest open relevant requests, with baseline-absent requests first."""
    earliest = now + timedelta(hours=min_hours)
    known = set(baseline_ids)
    # published is unparsed source text (often date-only); the numeric ID orders new requests.
    rows = [row for row in _open_buyict(intelligence_db)
            if row[0] > earliest and row[1].startswith(('LH-', 'PCS-'))]
    rows.sort(key=lambda row: (row[1] not in known, int(re.search(r'\d+$', row[1]).group())
                              if re.search(r'\d+$', row[1]) else -1, row[1]), reverse=True)
    return [row[1] for row in rows[:limit]]


def clean(text):
    """The layout check rejects U+FFFD; source text with a damaged character must not block the brief."""
    return str(text or '').replace('�', '-').strip()


def restricted_hint(record):
    content = record.get('content') if isinstance(record, dict) else None
    if not isinstance(content, dict):
        return False
    text = json.dumps({k: content.get(k) for k in ('description', 'criteriaText', 'additionalRequirements',
                                                   'response_requirements')}, ensure_ascii=False)
    return bool(RESTRICTED.search(text))


def still_open(record, now, closing_time):
    # The record comes from the live site: an odd shape drops this listing, never the whole brief.
    data = record.get('content') if isinstance(record, dict) else None
    if not isinstance(data, dict):
        return False
    rows = data.get('mainDetails')
    details = {x['label']: x.get('value') for x in (rows if isinstance(rows, list) else [])
               if isinstance(x, dict) and isinstance(x.get('label'), str)}
    end = closing_time(details.get('RFQ closing date') or details.get('RFI closing date'))
    return bool(end and end > now + timedelta(hours=2) and data.get('rfqOpen') and not data.get('is_cancelled'))


def editorial_notes(edition_day, verified, baseline_ids, report):
    """Templated notes; compose adds closing times and key listing details."""
    date_text = edition_day.strftime('%d %B %Y')
    count = len(verified)
    opportunities = {}
    for key, record in verified.items():
        notes = []
        if restricted_hint(record):
            notes.append('The listing text suggests responses are limited to invited or selected sellers; '
                         'check existing access before preparing a response.')
        opportunities[key] = notes
    gaps = [x['spider'] for x in report.get('results', []) if x.get('status') != 'OK']
    return {
        'edition_date': edition_day.isoformat(),
        'subject': f'Illoura daily brief | {date_text} | {count} newest opening{"s" if count != 1 else ""}',
        'preheader': f'{count} newest BuyICT opening{"s" if count != 1 else ""}, rechecked this morning.',
        'introduction': ('The newest open BuyICT requests, each rechecked on its public listing this morning.'),
        'opportunities': opportunities,
        'new_since_baseline': [key for key in verified if key not in set(baseline_ids)],
        'additional_sections': [],
        'coverage_note': ('Selection was automatic (no human editorial review); listings that failed the live '
                          'check were left out.' + (' Source gaps: ' + ', '.join(gaps) + '.' if gaps else '')),
    }


# --------------------------------------------------------------------------- phases

def kill_tree(process):
    if os.name == 'nt':
        process.kill()
    else:
        # run_spiders starts new sessions: include those descendant groups too.
        parents = {}
        for entry in Path('/proc').glob('[0-9]*/stat'):
            try:
                fields = entry.read_text().rsplit(')', 1)[1].split()
                parents[int(entry.parent.name)] = int(fields[1])
            except (OSError, ValueError, IndexError):
                continue
        descendants = {process.pid}
        while True:
            found = {pid for pid, parent in parents.items() if parent in descendants}
            if found <= descendants:
                break
            descendants.update(found)
        groups = {process.pid}
        for pid in descendants:
            try:
                groups.add(os.getpgid(pid))
            except ProcessLookupError:
                pass
        groups.discard(os.getpgrp())
        for group in groups:
            try:
                os.killpg(group, signal.SIGKILL)
            except ProcessLookupError:
                pass
    process.wait()



def bounded_command(command, timeout, **kwargs):
    """Keep stdout in the journal; capture stderr so guard failures remain identifiable."""
    process = subprocess.Popen(command, start_new_session=os.name != 'nt',
                               stderr=subprocess.PIPE, text=True, **kwargs)
    try:
        _, error = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(process)
        process.communicate()
        raise
    if error:
        print(error, file=sys.stderr, end='', flush=True)
    if process.returncode:
        raise subprocess.CalledProcessError(process.returncode, command, stderr=error)


def scraper(*args, env=None, timeout=None):
    command = ['runuser', '-u', 'scraper', '--', str(PYTHON), *args]
    if timeout is None:
        subprocess.run(command, check=True, cwd=str(WORK), env=env)
    else:
        bounded_command(command, timeout, cwd=str(WORK), env=env)


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
    baseline_file = out / 'baseline_buyict_ids.json'
    baseline = json.loads(baseline_file.read_text()) if baseline_file.exists() else []
    for key in select_ids(intelligence, now, baseline_ids=baseline):
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
    report = json.loads((out / 'source_refresh.json').read_text(encoding='utf-8'))
    notes = editorial_notes(edition_day, verified, baseline, report)
    new_request_count = len(notes['new_since_baseline'])
    try:
        from market_research_context import read_market_context
    except ImportError:
        market_sections,market_status = [],{'status':'unavailable','reason':'market reader not installed'}
    else:
        market_sections,market_status = read_market_context(WORK / 'market_research.json',edition_day,now)
    write_json(out / 'market_research_status.json',market_status)
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
               'new_since_last_run': new_request_count}
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


def _json_object(path):
    """The JSON object in a file, or None when it is missing, unreadable or another type."""
    try:
        value = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def send_state(folder):
    """'sent', 'unresolved' or None, by the same rules as illoura_cloud_collect.unresolved_send_runs.

    Only proof settles a send: a readable receipt saying sent is true, or a readable
    operator decision saying delivered is false. Damaged files prove nothing.
    """
    if receipt_confirms_send(folder):
        return 'sent'
    if not (folder / 'send_attempt.json').exists():
        return None
    decision = _json_object(folder / 'send_reconciliation.json')
    if decision is not None and decision.get('delivered') is False:
        return None
    return 'unresolved'


def check_edition(out):
    edition = json.loads((out / 'editorial_notes.json').read_text())['edition_date']
    datetime.strptime(edition, '%Y-%m-%d')
    for other in (WORK / 'reports').iterdir():
        if other.resolve() == out or not other.is_dir():
            continue
        state = send_state(other)
        if state is None:
            continue  # no send evidence: damaged or missing files there do not matter
        notes = _json_object(other / 'editorial_notes.json')
        other_edition = notes.get('edition_date') if notes else None
        if not isinstance(other_edition, str):
            # Edition unknown. A confirmed send counts when its own send time falls on this
            # edition's India date (a receipt that cannot be dated cannot be attributed, and
            # no command could ever clear it). An unsettled attempt always needs a person.
            if state == 'sent':
                receipt = _json_object(other / 'send_receipt.json') or {}
                try:
                    sent_day = datetime.fromisoformat(receipt['recorded_at_utc']).astimezone(INDIA).date()
                except (KeyError, TypeError, ValueError):
                    sent_day = None
                if sent_day is None or sent_day.isoformat() != edition:
                    continue
                raise SystemExit('A run with unreadable notes was sent on this edition date; check it before sending')
            raise SystemExit('A run with unreadable notes has an unresolved send attempt; reconcile it first')
        if other_edition != edition:
            continue
        if state == 'sent':
            raise SystemExit('This edition was already sent from another run')
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


def addresses(header):
    """Lower-case mail addresses in a To/From header, without display names."""
    from email.utils import getaddresses
    return {address.lower() for _, address in getaddresses([header]) if address}


def find_sent(session, attempt):
    api = 'https://gmail.googleapis.com/gmail/v1/users/me'

    def get(path, **params):
        response = session.get(api + path, params=params, timeout=45)
        response.raise_for_status()
        return response.json()

    sender, recipient, subject = (attempt[key] for key in ('sender', 'recipient', 'subject'))
    sender, recipient = sender.strip(), recipient.strip()
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
                        and recipient.lower() in addresses(headers.get('to', ''))
                        and sender.lower() in addresses(headers.get('from', ''))):
                    return message, searched
            page = matches.get('nextPageToken')
            if not page:
                break
    return None, searched


def receipt_confirms_send(out):
    """True only for a readable receipt saying sent is true; a damaged file proves nothing."""
    try:
        value = json.loads((out / 'send_receipt.json').read_text())
    except (OSError, ValueError):
        return False
    return isinstance(value, dict) and value.get('sent') is True


def reconcile(out, mark_undelivered=False, session=None):
    original = Path(out).absolute()
    out = original.resolve()
    if (original.is_symlink() or out.parent != (WORK / 'reports').resolve()
            or not out.name.startswith('daily_sweep_') or not out.is_dir()):
        raise SystemExit('Unexpected reconciliation run directory')
    with send_lock():
        if receipt_confirms_send(out):
            print(json.dumps({'status': 'already_sent'}))
            return 0
        if not (out / 'send_attempt.json').exists():
            print(json.dumps({'status': 'nothing_to_reconcile'}))
            return 0
        attempt = _json_object(out / 'send_attempt.json')
        if attempt is None or not all(isinstance(attempt.get(k), str)
                                      for k in ('sender', 'recipient', 'subject', 'message_id_header')):
            # A send killed while writing its attempt record: Gmail cannot be searched
            # without it, so only an explicit operator decision can settle it.
            if not mark_undelivered:
                print(json.dumps({'status': 'attempt_unreadable', 'next': 'check the mailbox, then '
                                  'reconcile-send --mark-undelivered if it did not arrive'}))
                return 2
            write_json(out / 'send_reconciliation.json', {
                'delivered': False, 'checked_at_utc': utc_now().isoformat(), 'searched': [],
                'attempt_unreadable': True, 'decided_by': 'operator'})
            print(json.dumps({'status': 'marked_undelivered', 'attempt_unreadable': True}))
            return 0
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


def unresolved_guard(error):
    try:
        state = json.loads((WORK / 'coordinator_status.json').read_text())
    except (OSError, ValueError):
        state = {}
    message = str(getattr(error, 'stderr', '') or '')
    return ('Unresolved sends require reconciliation' in message
            or (state.get('status') == 'failed'
                and state.get('error_type') in ('UnresolvedSendError', 'UnresolvedSendsError')))


def run_stage(stage, action, cutoff):
    """Retries for collection (four) and composition (one); always retain the original morning cutoff."""
    limit = COLLECT_TIMEOUT_SECONDS if stage == 'collect' else COMPOSE_TIMEOUT_SECONDS
    reserve = COMPOSE_TIMEOUT_SECONDS if stage == 'collect' else 0
    pauses = RETRY_PAUSES[stage]
    for attempt in range(len(pauses) + 1):
        try:
            remaining = (cutoff - utc_now()).total_seconds() - SEND_MARGIN_SECONDS - reserve
            if remaining <= 0:
                raise subprocess.TimeoutExpired(stage, 0)
            action(min(limit, remaining))
            return
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as error:
            now = utc_now()
            last = attempt >= len(pauses)
            pause = pauses[-1] if last else pauses[attempt]
            retry_at = now + timedelta(seconds=pause)
            latest = cutoff.replace(hour=LATEST_RETRY_START[0], minute=LATEST_RETRY_START[1])
            guard = stage == 'collect' and unresolved_guard(error)
            enough = retry_at <= latest and retry_at + timedelta(
                seconds=limit + reserve + SEND_MARGIN_SECONDS) <= cutoff
            retry = not last and not guard and enough
            reason = ('unresolved_sends' if guard else 'retry_exhausted' if last else
                      'within_budget' if enough else 'insufficient_time')
            print(json.dumps({'event': 'retry_decision', 'stage': stage, 'retry': retry,
                              'reason': reason, 'attempt': attempt + 1,
                              'at_utc': now.isoformat(), 'retry_at_utc': retry_at.isoformat()}), flush=True)
            if not retry:
                raise
            time.sleep(pause)
            # Sleep or clock movement can consume the allowance: check again at retry time.
            now = utc_now()
            if now > latest or now + timedelta(seconds=limit + reserve + SEND_MARGIN_SECONDS) > cutoff:
                print(json.dumps({'event': 'retry_decision', 'stage': stage, 'retry': False,
                                  'reason': 'insufficient_time_after_pause', 'at_utc': now.isoformat()}), flush=True)
                raise


def refresh_market_research(cutoff, target=None):
    """An optional research failure is recorded; it never prevents the RFQ composition stage.

    Bounded by the noon cutoff and, when given, by the 08:15 send target: it ends early enough for the
    whole compose bound plus a reserve before the send, or it is skipped (yesterday's package stays).
    """
    helper=OPS/'illoura_market_refresh.py'
    if not helper.is_file() or not Path('/etc/illoura-market/refresh.json').is_file():
        return {'status':'disabled'}
    now=utc_now()
    remaining=(cutoff-now).total_seconds()-SEND_MARGIN_SECONDS-COMPOSE_TIMEOUT_SECONDS
    if target is not None:
        remaining=min(remaining,(target-now).total_seconds()-COMPOSE_TIMEOUT_SECONDS-SEND_ON_TIME_RESERVE_SECONDS)
    if remaining<35:
        return {'status':'skipped','reason':'morning budget reserved for live RFQs and the on-time send'}
    limit=min(RESEARCH_CAP_SECONDS,int(remaining)-5)
    try:
        bounded_command([str(PYTHON),str(helper),'--timeout',str(limit)],limit+5,cwd=str(PROJECT))
        return {'status':'completed'}
    except (subprocess.CalledProcessError,subprocess.TimeoutExpired,OSError):
        return {'status':'failed','reason':'research refresh unavailable; existing dated package may still be used'}


def run(systemctl=None):
    """Root, from the timer: collect + publish, compose as scraper, wait for 08:15, send as scraper."""
    send_target, morning_cutoff = send_window(utc_now())
    env = dict(os.environ, ILLOURA_MORNING_DEADLINE=str(
        (morning_cutoff - timedelta(seconds=SEND_MARGIN_SECONDS + COMPOSE_TIMEOUT_SECONDS)).timestamp()))
    run_stage('collect', lambda timeout: bounded_command(
        [str(PYTHON), str(OPS / 'illoura_cloud_collect.py')], timeout, cwd=str(PROJECT), env=env), morning_cutoff)
    out = json.loads((WORK / 'latest_run.json').read_text())['run_dir']
    print(json.dumps({'event':'market_research_refresh',**refresh_market_research(morning_cutoff,send_target)}),flush=True)
    run_stage('compose', lambda timeout: scraper(
        str(OPS / 'illoura_daily_brief.py'), 'compose', '--out', out, timeout=timeout), morning_cutoff)
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
    scraper(str(OPS / 'illoura_daily_brief.py'), 'send', '--out', out,
            timeout=min(SEND_MARGIN_SECONDS, (cutoff - now).total_seconds()))


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
