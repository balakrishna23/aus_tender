"""Internal BuyICT alerts and evidence-linked BDM results. No buyer outreach.

Run `python bdm_monitor.py --help`. State lives separately from source databases.
The first successful observation is a silent baseline. Delivery is opt-in.
"""
import argparse
import base64
import csv
import hashlib
import html
import json
import os
import sqlite3
import subprocess
import sys
import uuid
from contextlib import contextmanager, closing, ExitStack
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

from classify import closing_time
from project_paths import PROJECT_DIR, database_directory

UTC = timezone.utc
HUMAN = ('owner', 'stage', 'follow_up', 'notes', 'outcome')
STAGES = ('identified', 'qualified', 'preparing', 'submitted', 'won', 'lost', 'no fit', 'on hold')
DEFAULTS = {
    'poll_minutes': 15, 'send_enabled': False,
    # Real addresses live only in the private config or environment, never in source.
    'sender': '', 'recipient': '',
    'oauth_token_file': '', 'include_prefixes': ['LH-', 'PCS-', 'RFI-'],
    'title_keywords': [], 'exclude_keywords': [], 'digest_limit': 20,
    'max_source_age_minutes': 45, 'healthcheck_url': '',
    'desktop_enabled': False, 'alert_since_utc': '', 'alert_max_age_hours': 24,
    'busy_retry_seconds': 60, 'busy_retries': 2,
    'workbench_enabled': False,
}
FIELDS = ('rfq_id', 'title', 'buyer', 'rfq_published_date', 'rfq_closing_date',
          'deadline_for_asking_questions', 'estimated_start_date', 'location_of_work',
          'working_arrangements', 'initial_contract_duration', 'extension_term',
          'number_of_extensions', 'requirements', 'criteria', 'description', 'key_duties',
          'response_requirements', 'all_details', 'status', 'url')
JSON_FIELDS = {'requirements', 'criteria', 'response_requirements', 'all_details'}
EXPORT_COLUMNS = ('action_id', 'source', 'source_id', 'title', 'organisation', 'priority',
                  'source_status', 'owner', 'stage', 'follow_up', 'next_action', 'deadline_canberra',
                  'deadline_ist', 'location', 'clearance', 'candidate_cap', 'notes', 'outcome',
                  'evidence_url', 'evidence_detail', 'retrieved_at', 'updated_at')
SCHEMA = '''
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations (
 source_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 payload TEXT NOT NULL, is_open INTEGER NOT NULL, relevant INTEGER NOT NULL, observed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS events (
 event_id TEXT PRIMARY KEY, source_id TEXT NOT NULL, revision INTEGER NOT NULL,
 kind TEXT NOT NULL, changed_fields TEXT NOT NULL, payload TEXT NOT NULL,
 created_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', batch_id TEXT,
 UNIQUE(source_id,revision));
CREATE TABLE IF NOT EXISTS deliveries (
 batch_id TEXT PRIMARY KEY, message_id TEXT NOT NULL, status TEXT NOT NULL,
 created_at TEXT NOT NULL, gmail_id TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS results (
 action_id TEXT PRIMARY KEY, source TEXT NOT NULL, source_id TEXT NOT NULL,
 machine TEXT NOT NULL, owner TEXT NOT NULL, stage TEXT NOT NULL DEFAULT 'identified',
 follow_up TEXT, notes TEXT, outcome TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS edit_history (
 action_id TEXT, field TEXT, old_value TEXT, new_value TEXT, changed_at TEXT);
'''


def now_utc():
    return datetime.now(UTC)


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def connect(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    from bdm_workflow import setup
    setup(db)
    return db


@contextmanager
def lock(path):
    """OS-held lock: released on a crash; shared by poll, edits and delivery."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open('a+b')
    try:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        raise RuntimeError('Another BDM monitor operation is running') from None
    try:
        yield
    finally:
        handle.close()


def internal_mailbox_pair(sender, recipient):
    """Private internal alerting only, never a buyer-email engine.

    Exactly one recipient, on the sender's own email domain. Returns the
    normalised pair, or raises ValueError.
    """
    sender = str(sender or '').strip().lower()
    recipient = str(recipient or '').strip().lower()
    if not sender or not recipient:
        raise ValueError('Configure the internal sender and recipient in the private monitor '
                         'config, or set ILLOURA_MAIL_SENDER and ILLOURA_MAIL_RECIPIENT')
    for address in (sender, recipient):
        local, at, domain = address.partition('@')
        if (not local or not at or '.' not in domain or '@' in domain
                or any(ch in address for ch in ',; <>')):
            raise ValueError('Sender and recipient must each be one plain email address')
    if sender.rpartition('@')[2] != recipient.rpartition('@')[2]:
        raise ValueError("This pilot is restricted to one internal recipient on the sender's own domain")
    return sender, recipient


def config(path):
    value = dict(DEFAULTS)
    value['sender'] = os.environ.get('ILLOURA_MAIL_SENDER', '')
    value['recipient'] = os.environ.get('ILLOURA_MAIL_RECIPIENT', '')
    if path:
        import yaml
        value.update(yaml.safe_load(path.read_text(encoding='utf-8')) or {})
    if value['poll_minutes'] < 15:
        raise ValueError('Minimum supported poll interval is 15 minutes')
    if not 1 <= value['digest_limit'] <= 100:
        raise ValueError('digest_limit must be between 1 and 100')
    # Previews may run unconfigured; delivery, or any configured address, must pass the guard.
    if value['send_enabled'] or value['sender'] or value['recipient']:
        value['sender'], value['recipient'] = internal_mailbox_pair(value['sender'], value['recipient'])
    return value


def read_rows(path, query, params=()):
    with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(query, params)]


def normalize(row):
    value = {k: row.get(k) for k in FIELDS}
    for k in JSON_FIELDS:
        if isinstance(value[k], str):
            try:
                value[k] = json.loads(value[k])
            except ValueError:
                pass
    return value


def is_open(value, moment):
    end = closing_time(value.get('rfq_closing_date'))
    return bool(end and end > moment and str(value.get('status')).lower() == 'open')


def relevant(value, cfg):
    title = (value.get('title') or '').lower()
    return (any((value.get('rfq_id') or '').startswith(p) for p in cfg['include_prefixes'])
            and not any(k.lower() in title for k in cfg['exclude_keywords'])
            and (not cfg['title_keywords'] or any(k.lower() in title for k in cfg['title_keywords'])))


def deadline(text, zone):
    moment = closing_time(text)
    return moment.astimezone(ZoneInfo(zone)).strftime('%d %b %Y %H:%M %Z') if moment else ''


def buyict_action(row, moment, cfg):
    value = normalize(row)
    opened = is_open(value, moment)
    end = closing_time(value.get('rfq_closing_date'))
    req = value.get('requirements') or {}
    req = req if isinstance(req, dict) else {}
    hours = (end - moment).total_seconds() / 3600 if end else None
    return dict(
        action_id='BUYICT:' + value['rfq_id'], source='BuyICT', source_id=value['rfq_id'],
        title=value['title'] or value['rfq_id'], organisation=value['buyer'] or '',
        priority=('URGENT' if hours is not None and 0 < hours <= 48 else 'REVIEW') if opened else 'CLOSED',
        source_status='Open' if opened else 'Closed or unverified',
        next_action='Sirish: check seller invitation/access, full response pack and candidate skills, clearance, location and availability. Use formal Q&A for live work.' if opened else 'Do not submit without confirming that responses are open.',
        deadline_canberra=deadline(value['rfq_closing_date'], 'Australia/Sydney'),
        deadline_ist=deadline(value['rfq_closing_date'], 'Asia/Kolkata'),
        location=value['location_of_work'] or '', clearance=req.get('Security clearance', ''),
        candidate_cap=req.get('Maximum number of candidates per seller', ''),
        evidence_url=value['url'],
        evidence_detail=dump({'published': value['rfq_published_date'], 'start': value['estimated_start_date'],
                              'working_arrangements': value['working_arrangements'], 'requirements': req,
                              'criteria': value['criteria'], 'description': value['description'],
                              'additional_public_details': value['all_details'],
                              'fit': 'Matches configured alert filters; candidate fit and invitation access unverified'}),
        retrieved_at=row.get('scraped_at') or '')


def upsert_result(db, machine, owner, moment):
    db.execute('''INSERT INTO results(action_id,source,source_id,machine,owner,updated_at)
                  VALUES(?,?,?,?,?,?) ON CONFLICT(action_id) DO UPDATE SET
                  machine=excluded.machine,updated_at=excluded.updated_at''',
               (machine['action_id'], machine['source'], machine['source_id'], dump(machine), owner, moment.isoformat()))


def observe(db, rows, cfg, moment):
    """One atomic observation. Never infer closure merely because a row is missing."""
    if not rows:
        raise ValueError('Empty source snapshot; refusing to advance baseline')
    keys = [r.get('sys_id') or r.get('rfq_id') for r in rows]
    if not all(keys) or len(keys) != len(set(keys)):
        raise ValueError('Missing or duplicate BuyICT source identities')
    initialized = db.execute("SELECT value FROM meta WHERE key='baseline_at'").fetchone() is not None
    made = 0
    with db:
        for row, key in zip(rows, keys):
            value = normalize(row)
            opened, fits = is_open(value, moment), relevant(value, cfg)
            fingerprint = hashlib.sha256(dump(value).encode()).hexdigest()
            old = db.execute('SELECT * FROM observations WHERE source_id=?', (key,)).fetchone()
            changed = [k for k in FIELDS if not old or json.loads(old['payload']).get(k) != value[k]]
            state_changed = old and (old['is_open'] != int(opened) or old['relevant'] != int(fits))
            revision = (old['revision'] + 1) if old and (old['fingerprint'] != fingerprint or state_changed) else (old['revision'] if old else 1)
            event_kind = None
            if initialized and (changed or state_changed):
                if opened and fits:
                    event_kind = 'newly_discovered' if not old else ('reopened' if not old['is_open'] else 'changed')
                elif old and old['is_open'] and old['relevant'] and not opened:
                    event_kind = 'closed_or_expired'
            if changed or state_changed:
                # Coalesce undelivered revisions, so an old deadline is never emailed later.
                db.execute("UPDATE events SET status='superseded' WHERE source_id=? AND status='pending'", (key,))
            if event_kind:
                db.execute('''INSERT INTO events(event_id,source_id,revision,kind,changed_fields,payload,created_at)
                              VALUES(?,?,?,?,?,?,?)''', (uuid.uuid4().hex, key, revision, event_kind,
                                                         dump(changed), dump(value), moment.isoformat()))
                made += 1
            db.execute('''INSERT INTO observations VALUES(?,?,?,?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET
                          revision=excluded.revision,fingerprint=excluded.fingerprint,payload=excluded.payload,
                          is_open=excluded.is_open,relevant=excluded.relevant,observed_at=excluded.observed_at''',
                       (key, revision, fingerprint, dump(value), int(opened), int(fits), moment.isoformat()))
            if fits and (opened or db.execute('SELECT 1 FROM results WHERE action_id=?', ('BUYICT:' + value['rfq_id'],)).fetchone()):
                upsert_result(db, buyict_action(row, moment, cfg), 'Sirish', moment)
        db.execute("INSERT OR IGNORE INTO meta VALUES('baseline_at',?)", (moment.isoformat(),))
        db.execute("INSERT OR REPLACE INTO meta VALUES('last_observation_at',?)", (moment.isoformat(),))
    return {'baseline_created': not initialized, 'observed': len(rows), 'new_events': made}


def sync_planning(db, data_dir, moment):
    """Weekly review inputs read locally; no automatic outreach or fresh-web claim."""
    today = moment.astimezone(ZoneInfo('Asia/Kolkata')).date()
    intel = data_dir / 'intelligence.db'
    rap = data_dir / 'rap_evidence.db'
    counts = {'AusTender': 0, 'RAP': 0}
    if intel.exists():
        opportunities = read_rows(intel, "SELECT * FROM opportunities WHERE source IN ('AusTender','AusTender plan') AND is_relevant=1")
        with db:
            # Mark previous planning records stale before refreshing; old records are retained.
            for stored in db.execute("SELECT action_id,machine FROM results WHERE source='AusTender'").fetchall():
                m = json.loads(stored['machine']); m.update(source_status='Not in current planning selection', priority='ARCHIVED')
                db.execute('UPDATE results SET machine=? WHERE action_id=?', (dump(m), stored['action_id']))
            for r in opportunities:
                end = closing_time(r['closes'])
                planned = r['source'] == 'AusTender plan'
                if not planned and not (r['status_effective'] == 'Open' and end and end > moment):
                    continue
                if planned:
                    if str(r.get('status_source') or '').lower() in ('withdrawn','cancelled','closed','to market'):
                        continue
                    # Current intelligence already selects still-listed plans. Limit to next 180 days.
                    if not r['expected_from'] or r['expected_from'][:10] > str(today + timedelta(days=180)) or (r['expected_to'] and r['expected_to'][:10] < str(today)):
                        continue
                key = r['source'] + ':' + str(r['source_id'])
                machine = dict(action_id=key, source='AusTender', source_id=r['source_id'], title=r['title'],
                               organisation=r['buyer'], source_status='Planned - verify release status' if planned else 'Open',
                               priority='PREPARE' if planned else 'REVIEW',
                               next_action='Sirish: review scope and timing; verify release status before any contact.' if planned else 'Sirish: verify response access, scope and capability; use formal procurement channels.',
                               deadline_canberra=deadline(r['closes'], 'Australia/Sydney'), deadline_ist=deadline(r['closes'], 'Asia/Kolkata'),
                               location=r['location'], evidence_url=r['url'], retrieved_at=r['retrieved_at'],
                               evidence_detail=dump({'expected_from':r['expected_from'],'expected_to':r['expected_to'], 'relevance_reason':r['relevance_reason']}))
                upsert_result(db, machine, 'Sirish', moment); counts['AusTender'] += 1
    if rap.exists():
        partners = read_rows(rap, '''SELECT * FROM company_reviews r WHERE reviewed_on=
                           (SELECT max(reviewed_on) FROM company_reviews x WHERE x.organisation=r.organisation)''')
        with db:
            for r in partners:
                p = json.loads(r['payload_json']); current = r['period_start'] <= str(today) <= r['period_end']
                hooks = []
                if intel.exists() and p.get('candidate_abns'):
                    abns = p['candidate_abns']
                    hooks = read_rows(intel, 'SELECT cn_id,buyer,description,current_end_date,page_url FROM renewals WHERE supplier_abn IN (' + ','.join('?' for _ in abns) + ') AND current_end_date BETWEEN ? AND ? ORDER BY current_end_date LIMIT 3', [*abns,str(today),str(today+timedelta(days=180))])
                machine = dict(action_id='RAP:' + p['organisation'], source='RAP', source_id=p['organisation'],
                               title=p['display_name'] + ' supplier preparation', organisation=p['organisation'],
                               priority='PREPARE' if current and p['priority']=='Prepare now' else 'VERIFY',
                               source_status='Within published plan period' if current else 'Outside published period - successor check',
                               next_action=p['next_action'] if current else 'Lane: obtain and review the current successor plan before using historical targets.',
                               evidence_url=p['document']['source_url'], retrieved_at=p['reviewed_on'],
                               evidence_detail=dump({'reviewed_on':p['reviewed_on'], 'period_start':r['period_start'], 'period_end':r['period_end'],
                                                     'commitments':p['findings'], 'contract_hooks':hooks,
                                                     'limits':'Published commitments and stored contract end dates are research leads, not buying requirements, unmet targets or confirmed RAP entity scope.'}))
                upsert_result(db, machine, 'Lane', moment); counts['RAP'] += 1
    return counts


def update_edits(db, rows, moment):
    """Validate the entire import before changing anything; source fields ignored."""
    changes = []
    for r in rows:
        if str(r.get('action_id') or '').startswith('LEGACY:'):
            raise ValueError('Edit legacy account actions in the working view so changes also reach the original tracker')
        old = db.execute('SELECT * FROM results WHERE action_id=?', (r.get('action_id'),)).fetchone()
        if not old:
            raise ValueError('Unknown action_id in edit import')
        for field in HUMAN:
            if field not in r:
                continue
            raw=r[field]
            if field=='follow_up' and isinstance(raw,datetime):
                raw=raw.date().isoformat()
            value = str(raw or '').strip()
            if field == 'stage' and value not in STAGES:
                raise ValueError('Invalid stage: ' + value)
            if field == 'owner' and value not in ('Lane', 'Sirish'):
                raise ValueError('Owner must be Lane or Sirish')
            if field == 'follow_up' and value:
                datetime.strptime(value, '%Y-%m-%d')
            if value != (old[field] or ''):
                changes.append((r['action_id'], field, old[field], value))
    with db:
        for key, field, old, value in changes:
            db.execute(f'UPDATE results SET {field}=?,updated_at=? WHERE action_id=?', (value, moment.isoformat(), key))
            db.execute('INSERT INTO edit_history VALUES(?,?,?,?,?)', (key,field,old,value,moment.isoformat()))
    return len(changes)


def load_edits(path):
    """Read exported CSV or a saved Results workbook, including its title rows."""
    if path.suffix.lower()=='.xlsx':
        from openpyxl import load_workbook
        book=load_workbook(path,read_only=True,data_only=False)
        try:
            if 'Results' not in book.sheetnames:
                raise ValueError('Workbook must have a Results sheet')
            matrix=list(book['Results'].values)
        finally:
            book.close()
    else:
        with path.open(encoding='utf-8-sig',newline='') as f:
            matrix=list(csv.reader(f))
    aliases={'action id':'action_id','follow up (yyyy-mm-dd)':'follow_up'}
    for number,row in enumerate(matrix[:20]):
        headers=[aliases.get(str(v or '').strip().lower(),str(v or '').strip().lower()) for v in row]
        if 'action_id' in headers:
            return [{k:v for k,v in zip(headers,values) if k in ('action_id',*HUMAN)}
                    for values in matrix[number+1:] if any(v is not None and str(v).strip() for v in values)]
    raise ValueError('No action_id / Action ID header found')


def result_rows(db):
    result = []
    for row in db.execute('SELECT * FROM results'):
        value = json.loads(row['machine'])
        value.update({k:row[k] for k in HUMAN}); value['updated_at'] = row['updated_at']
        result.append({k:value.get(k) or '' for k in EXPORT_COLUMNS})
    order = {'URGENT':0, 'PREPARE':1, 'REVIEW':2, 'VERIFY':3, 'CLOSED':4, 'ARCHIVED':5}
    return sorted(result, key=lambda r:(r['stage'] in ('won','lost','no fit'), order.get(r['priority'],9), r['source'], r['source_id']))


def export_results(db, out, moment):
    out.mkdir(parents=True, exist_ok=True)
    from bdm_workflow import rows as workflow_rows, FIELDS as QUALIFICATION_FIELDS
    rows = workflow_rows(db,moment)
    columns = (*EXPORT_COLUMNS, *QUALIFICATION_FIELDS, 'active', 'overdue', 'qualification_score')
    atomic_json(out/'results.json', {'generated_at_utc':moment.isoformat(), 'columns':columns, 'rows':rows})
    # Excel-safe CSV; leading formula characters must remain literal text.
    with (out/'results.csv').open('w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=columns); writer.writeheader()
        for row in rows:
            flat = dict(row,**{k:row['qualification'][k] for k in QUALIFICATION_FIELDS})
            writer.writerow({k: "'"+str(flat.get(k,'')) if str(flat.get(k,'')).lstrip().startswith(('=','+','-','@')) else flat.get(k,'') for k in columns})
    # Small working extracts; retained history stays in the master ledger.
    for filename, active in [('active_results.csv',True),('archived_results.csv',False)]:
        with (out/filename).open('w', newline='', encoding='utf-8-sig') as f:
            writer=csv.DictWriter(f,fieldnames=columns);writer.writeheader()
            for row in rows:
                if row['active'] != active:
                    continue
                flat=dict(row,**{k:row['qualification'][k] for k in QUALIFICATION_FIELDS})
                writer.writerow({k: "'"+str(flat.get(k,'')) if str(flat.get(k,'')).lstrip().startswith(('=','+','-','@')) else flat.get(k,'') for k in columns})
    return len(rows)


def pending_events(db, cfg, moment):
    events = []
    with db:
        for row in db.execute("SELECT * FROM events WHERE status='pending' ORDER BY created_at,event_id").fetchall():
            value = json.loads(row['payload'])
            if row['kind'] != 'closed_or_expired' and (not is_open(value,moment) or not relevant(value,cfg)):
                db.execute("UPDATE events SET status='superseded' WHERE event_id=?", (row['event_id'],))
            else:
                events.append(dict(row))
    if cfg.get('alert_since_utc'):
        cutoff = datetime.fromisoformat(cfg['alert_since_utc'])
        events = [e for e in events if datetime.fromisoformat(e['created_at']) >= cutoff]
    return events[:cfg['digest_limit']]


def make_message(events, cfg, message_id):
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'], msg['Message-ID'] = cfg['sender'], cfg['recipient'], f'Illoura BuyICT alert | {len(events)} new or changed requirements', message_id
    lines = ['Hi Sirish,', '', 'Public BuyICT changes detected. Check invitation access and candidate fit before committing.', '']
    for row in events:
        v = json.loads(row['payload']); req = v.get('requirements') or {}
        requirements_text='; '.join(f'{k}: {val}' for k,val in req.items()) if isinstance(req,dict) else str(req)
        extra=v.get('all_details') or {}
        notes=[r.get('value','') for r in extra.get('public_requirements',[]) if isinstance(r,dict) and not r.get('label')] if isinstance(extra,dict) else []
        response=v.get('response_requirements') or []
        response_text=', '.join(str(x) for x in response) if isinstance(response,list) else str(response)
        changed=', '.join(k.replace('rfq_','').replace('_',' ') for k in json.loads(row['changed_fields'])) or 'Sample preview; no change event queued'
        lines.extend([f"{row['kind'].replace('_',' ').upper()}: {v['rfq_id']} | {v['title']}", f"Buyer: {v['buyer']}",
                      'Published: '+str(v.get('rfq_published_date') or 'Not stated'),
                      'Closes: '+deadline(v['rfq_closing_date'],'Australia/Sydney')+' / '+deadline(v['rfq_closing_date'],'Asia/Kolkata'),
                      f"Start: {v.get('estimated_start_date') or 'Not stated'}; Location: {v.get('location_of_work') or 'Not stated'}; Work: {v.get('working_arrangements') or 'Not stated'}",
                      'Questions close: '+deadline(v.get('deadline_for_asking_questions'),'Australia/Sydney')+' / '+deadline(v.get('deadline_for_asking_questions'),'Asia/Kolkata'),
                      'Requirements: '+requirements_text,
                      'Scope excerpt: '+(v.get('description') or 'Read the full official listing.')[:650],
                      'Attendance notes: '+(' '.join(notes) or 'Check the complete pack.'),
                      'Response format: '+response_text,
                      'Changed: '+changed,
                      'Next action: stop response preparation; verify current public status.' if row['kind']=='closed_or_expired' else 'Next action: Sirish qualifies skills, clearance, availability and full response criteria. Use formal Q&A for live work.',
                      v['url'] or '', ''])
    lines.append('Public visibility does not establish seller invitation, secure attachments or candidate availability. Newly discovered does not necessarily mean newly published. No buyer or partner outreach was sent.')
    text = '\n'.join(lines)
    msg.set_content(text)
    msg.add_alternative('<html><head><meta name="viewport" content="width=device-width,initial-scale=1"></head><body style="margin:0;padding:18px;font:15px Arial;line-height:1.5"><main style="max-width:680px;margin:auto"><h2>Illoura BuyICT alert</h2><div style="white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word">'+html.escape(text)+'</div></main></body></html>', subtype='html')
    return msg


class Gmail:
    """Dedicated installed-app OAuth credentials, independent of the chat connector."""
    def __init__(self, cfg):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import AuthorizedSession
        token_path = Path(cfg['oauth_token_file']).expanduser()
        if not token_path.is_file():
            raise ValueError('Configure a private Gmail OAuth token file before delivery')
        self.session = AuthorizedSession(Credentials.from_authorized_user_file(str(token_path)))
        self.root = 'https://gmail.googleapis.com/gmail/v1/users/me'
        profile = self.session.get(self.root+'/profile', timeout=30)
        profile.raise_for_status()
        if profile.json().get('emailAddress','').lower() != cfg['sender']:
            raise ValueError('OAuth account does not match configured internal sender')

    def send(self, message):
        response = self.session.post(self.root+'/messages/send', json={'raw':base64.urlsafe_b64encode(message.as_bytes()).decode()}, timeout=45)
        response.raise_for_status()
        value = response.json()
        if not value.get('id'):
            raise RuntimeError('No Gmail message ID returned')
        return value['id']

    def find(self, message_id):
        response = self.session.get(self.root+'/messages', params={'q':'in:sent rfc822msgid:'+message_id.strip('<>')}, timeout=30)
        response.raise_for_status()
        return [m['id'] for m in response.json().get('messages', [])]


def deliver(db, cfg, out, moment, transport=None):
    if not cfg['send_enabled']:
        raise ValueError('Delivery disabled; enable send_enabled only when ready to activate internal alerts')
    # Re-check here too: callers may pass a config dict that never went through config().
    internal_mailbox_pair(cfg['sender'], cfg['recipient'])
    # Do not let an older ambiguous send race a new digest.
    if db.execute("SELECT 1 FROM deliveries WHERE status IN ('sending','uncertain')").fetchone():
        raise RuntimeError('Unresolved send; reconcile Gmail Sent before further delivery')
    observed = db.execute("SELECT value FROM meta WHERE key='last_live_refresh_at'").fetchone()
    if not observed or moment - datetime.fromisoformat(observed[0]) > timedelta(minutes=cfg['max_source_age_minutes']):
        raise ValueError('A recent successful live refresh is required before sending')
    events = pending_events(db,cfg,moment)
    if not events:
        return {'sent':0}
    transport = transport or Gmail(cfg)  # Account validation happens before the durable attempt.
    key = uuid.uuid4().hex; message_id = '<illoura-bdm-'+key+'@illouraservices.com>'
    message = make_message(events,cfg,message_id)
    out.mkdir(parents=True,exist_ok=True)
    (out/(key+'.eml')).write_bytes(message.as_bytes())
    with db:
        db.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?)', (key,message_id,'sending',moment.isoformat(),None,None))
        db.executemany("UPDATE events SET status='sending',batch_id=? WHERE event_id=?", [(key,e['event_id']) for e in events])
    try:
        gmail_id = transport.send(message)
    except Exception as exc:
        # A timeout/HTTP error may follow acceptance. Never automatically retry a send.
        with db:
            db.execute("UPDATE deliveries SET status='uncertain',error=? WHERE batch_id=?", (type(exc).__name__,key))
            db.execute("UPDATE events SET status='uncertain' WHERE batch_id=?", (key,))
        raise RuntimeError('Send result uncertain; reconcile before retrying. Batch '+key) from None
    with db:
        db.execute("UPDATE deliveries SET status='sent',gmail_id=? WHERE batch_id=?", (gmail_id,key))
        db.execute("UPDATE events SET status='sent' WHERE batch_id=?", (key,))
    return {'sent':len(events),'gmail_id':gmail_id,'batch_id':key}


class SourceBusy(RuntimeError):
    """A successful source runner declined to start because another run owns it."""


def refresh(data_dir, out, moment):
    report = out/('source_'+moment.strftime('%Y%m%dT%H%M%S')+'_'+uuid.uuid4().hex[:8]+'.json')
    report.parent.mkdir(parents=True,exist_ok=True)
    env = dict(os.environ, AUSTENDER_DB_DIR=str(data_dir.resolve()), GSHEETS_AUTO_SYNC='False')
    with report.with_suffix('.log').open('w',encoding='utf-8') as log:
        result = subprocess.run([sys.executable,'-u',str(PROJECT_DIR/'run_spiders.py'),'--spiders','buy_ict',
                                 '--timeout','600','--retries','1','--no-sync','--no-feed','--report',str(report.resolve())],
                                cwd=PROJECT_DIR,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1300)
    if not report.exists() and result.returncode == 0 and 'Another run is still in progress' in report.with_suffix('.log').read_text(encoding='utf-8',errors='replace'):
        raise SourceBusy('Source refresh deferred: another source run is active')
    if result.returncode or not report.exists():
        raise RuntimeError('BuyICT refresh failed or skipped because another source run holds the lock')
    value = json.loads(report.read_text(encoding='utf-8'))
    results = value.get('results',[])
    if not value.get('success') or len(results)!=1 or results[0]['spider']!='buy_ict' or results[0]['status']!='OK' or results[0].get('crawl_failed') or results[0].get('errors'):
        raise RuntimeError('BuyICT source report is incomplete or failed; baseline not advanced')
    return str(report)


def heartbeat(cfg, suffix):
    if cfg.get('healthcheck_url'):
        import requests
        try:
            requests.get(cfg['healthcheck_url'].rstrip('/')+suffix,timeout=10).raise_for_status()
        except Exception:
            return False  # Never log token-bearing monitoring URLs.
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path)
    parser.add_argument('--data-dir',type=Path,default=database_directory())
    parser.add_argument('--state',type=Path,default=database_directory()/'bdm_monitor.db')
    parser.add_argument('--out',type=Path,default=PROJECT_DIR/'exports/bdm')
    sub = parser.add_subparsers(dest='command',required=True)
    poll = sub.add_parser('poll'); poll.add_argument('--refresh',action='store_true'); poll.add_argument('--send',action='store_true')
    sub.add_parser('export'); sub.add_parser('status')
    preview=sub.add_parser('preview'); preview.add_argument('--sample',action='store_true')
    sub.add_parser('deliver'); sub.add_parser('reconcile')
    edit = sub.add_parser('import-edits'); edit.add_argument('edit_file',type=Path)
    args=parser.parse_args(); cfg=config(args.config); moment=now_utc()
    with ExitStack() as stack:
        refresh_report = None
        if args.command=='poll':
            # Crawl without holding the edit lock. Public-source collection can
            # take minutes; people can still qualify work during that interval.
            stack.enter_context(lock(args.state.with_suffix('.poll.lock')))
            if args.refresh:
                heartbeat(cfg,'/start')
                try:
                    refresh_report=refresh(args.data_dir,args.out/'runs',moment)
                except SourceBusy:
                    atomic_json(args.out/'health.json',{'status':'deferred','reason':'Source runner busy; observation baseline unchanged','deferred_at_utc':now_utc().isoformat()})
                    raise
                except Exception as exc:
                    atomic_json(args.out/'health.json',{'status':'failed','failed_at_utc':now_utc().isoformat(),'error_type':type(exc).__name__})
                    heartbeat(cfg,'/fail')
                    raise
        stack.enter_context(lock(args.state.with_suffix('.lock')))
        db=stack.enter_context(closing(connect(args.state)))
        result={}
        if args.command=='poll':
            try:
                if args.refresh:
                    result['refresh_report']=refresh_report
                    moment=now_utc()
                rows=read_rows(args.data_dir/'buy_ict.db','SELECT * FROM buy_ict_tenders')
                result.update(observe(db,rows,cfg,moment))
                if args.refresh:
                    with db:
                        db.execute("INSERT OR REPLACE INTO meta VALUES('last_live_refresh_at',?)", (moment.isoformat(),))
                result['planning']=sync_planning(db,args.data_dir,moment)
                import bdm_workflow as workflow
                result['legacy_actions'] = workflow.legacy_actions(db,args.data_dir,moment)
                result['reconciliation'] = workflow.reconcile(db,cfg,moment)
                result['results']=export_results(db,args.out,moment)
                result['desktop'] = workflow.desktop_alerts(db,cfg,moment)
                result['followup_reminders'] = workflow.due_reminders(db,cfg,moment)
                if args.send:
                    if not args.refresh:
                        raise ValueError('poll --send requires --refresh')
                    result['delivery']=deliver(db,cfg,args.out/'mail',moment)
                result.update(status='ok',completed_at_utc=now_utc().isoformat(),sending_enabled=cfg['send_enabled'])
                atomic_json(args.out/'health.json',result); heartbeat(cfg,'')
            except SourceBusy:
                atomic_json(args.out/'health.json',{'status':'deferred','reason':'Source runner busy; observation baseline unchanged','deferred_at_utc':now_utc().isoformat()})
                raise
            except Exception as exc:
                atomic_json(args.out/'health.json',{'status':'failed','failed_at_utc':now_utc().isoformat(),'error_type':type(exc).__name__})
                heartbeat(cfg,'/fail'); raise
        elif args.command=='export':
            result['results']=export_results(db,args.out,moment)
        elif args.command=='import-edits':
            result['edited_fields']=update_edits(db,load_edits(args.edit_file),moment)
            result['results']=export_results(db,args.out,moment)
        elif args.command=='preview':
            events=pending_events(db,cfg,moment); args.out.mkdir(parents=True,exist_ok=True)
            sample=False
            if not events and args.sample:
                stored=db.execute('SELECT payload FROM observations WHERE is_open=1 AND relevant=1 LIMIT 1').fetchone()
                if stored:
                    events=[dict(kind='sample_only',payload=stored[0],changed_fields='[]')]; sample=True
            message=make_message(events,cfg,'<preview@illouraservices.com>')
            message.replace_header('Subject','PREVIEW ONLY | '+message['Subject'])
            (args.out/'alert_preview.eml').write_bytes(message.as_bytes())
            (args.out/'alert_preview.txt').write_text(message.get_body(preferencelist=('plain',)).get_content(),encoding='utf-8')
            (args.out/'alert_preview.html').write_text(message.get_body(preferencelist=('html',)).get_content(),encoding='utf-8')
            result={'preview_items':len(events),'sample_only':sample,'sent':False}
        elif args.command=='deliver':
            result=deliver(db,cfg,args.out/'mail',moment)
        elif args.command=='reconcile':
            transport=Gmail(cfg); result={'resolved':0}
            for r in db.execute("SELECT * FROM deliveries WHERE status IN ('sending','uncertain')").fetchall():
                matches=transport.find(r['message_id'])
                if len(matches)==1:
                    with db:
                        db.execute("UPDATE deliveries SET status='sent',gmail_id=? WHERE batch_id=?", (matches[0],r['batch_id']))
                        db.execute("UPDATE events SET status='sent' WHERE batch_id=?", (r['batch_id'],))
                    result['resolved']+=1
                # No match is not permission to retry: search indexing can lag.
        else:
            result={'meta':dict(db.execute('SELECT key,value FROM meta')),
                    'events':dict(db.execute('SELECT status,count(*) FROM events GROUP BY status')),
                    'deliveries':dict(db.execute('SELECT status,count(*) FROM deliveries GROUP BY status')),
                    'results':db.execute('SELECT count(*) FROM results').fetchone()[0],
                    'sending_enabled':cfg['send_enabled']}
        print(json.dumps(result,indent=2))


if __name__=='__main__':
    try:
        main()
    except SourceBusy:
        print('Source refresh deferred: another run is active. Baseline unchanged.',file=sys.stderr)
        raise SystemExit(75)
    except Exception as exc:
        # Credentials, token-bearing URLs and HTTP response bodies must not enter logs.
        print('BDM monitor failed: '+type(exc).__name__+'. Inspect local health/source reports.',file=sys.stderr)
        raise SystemExit(1)
