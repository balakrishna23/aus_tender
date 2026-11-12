"""Qualification, stale-view reconciliation and local notifications for BDM."""
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

ENUMS = {
    'access': ('unknown', 'invited', 'request available', 'restricted', 'not applicable'),
    'capability': ('unknown', 'fit', 'partial', 'no fit'),
    'readiness': ('unknown', 'ready', 'needs checks', 'unavailable'),
    'decision': ('undecided', 'pursue', 'no pursue', 'hold'),
}
FIELDS = (*ENUMS, 'candidate_reference', 'clearance_verified_on', 'location_verified_on',
          'availability_verified_on', 'decision_reason', 'next_action_due', 'acknowledged_at')


def setup(db):
    db.executescript('''CREATE TABLE IF NOT EXISTS qualification (
      action_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
    CREATE TABLE IF NOT EXISTS desktop_receipts (
      event_id TEXT PRIMARY KEY, status TEXT NOT NULL, recorded_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS operational_notices (
      notice_key TEXT PRIMARY KEY, last_notified TEXT NOT NULL);
    ''')


def qualification(db, key):
    r = db.execute('SELECT payload,version FROM qualification WHERE action_id=?', (key,)).fetchone()
    value = {k: v[0] for k, v in ENUMS.items()}
    value.update({k: '' for k in FIELDS if k not in ENUMS})
    if r:
        value.update(json.loads(r['payload']))
    value['version'] = r['version'] if r else 0
    return value


def save_qualification(db, key, patch, expected_version, moment):
    if not db.execute('SELECT 1 FROM results WHERE action_id=?', (key,)).fetchone():
        raise ValueError('Unknown action')
    old = qualification(db, key)
    if expected_version != old['version']:
        raise ValueError('This card changed. Reload before saving.')
    value = {k: old[k] for k in FIELDS}
    for k, raw in patch.items():
        if k not in FIELDS:
            raise ValueError('Unknown qualification field')
        v = str(raw or '').strip()
        if len(v) > 4000:
            raise ValueError('Field is too long')
        if k in ENUMS and v not in ENUMS[k]:
            raise ValueError('Invalid ' + k)
        if (k.endswith('_on') or k == 'next_action_due') and v:
            datetime.strptime(v, '%Y-%m-%d')
        if k == 'acknowledged_at' and v:
            datetime.fromisoformat(v)
        value[k] = v
    if value['decision'] == 'no pursue' and not value['decision_reason']:
        raise ValueError('Record the reason for no pursue')
    if value['decision'] == 'pursue' and (not value['next_action_due'] or not value['decision_reason']):
        raise ValueError('Pursue needs a reason and next-action date')
    if value['readiness'] == 'ready' and not all(value[k] for k in
            ('candidate_reference', 'clearance_verified_on', 'location_verified_on', 'availability_verified_on')):
        raise ValueError('Ready needs candidate reference and dated clearance/location/availability checks')
    for k in FIELDS:
        if value[k] != old[k]:
            db.execute('INSERT INTO edit_history VALUES(?,?,?,?,?)',
                       (key, 'qualification.' + k, old[k], value[k], moment.isoformat()))
    db.execute('INSERT INTO qualification VALUES(?,?,?) ON CONFLICT(action_id) DO UPDATE SET payload=excluded.payload,version=excluded.version',
               (key, json.dumps(value), old['version'] + 1))


def reconcile(db, cfg, moment):
    """Hide elapsed records and superseded backlog; never delete source/history."""
    import bdm_monitor as m
    setup(db)
    counts = {'expired_results': 0, 'suppressed_events': 0}
    cutoff = datetime.fromisoformat(cfg['alert_since_utc']) if cfg.get('alert_since_utc') else None
    with db:
        for r in db.execute('SELECT * FROM results WHERE source=?', ('BuyICT',)).fetchall():
            machine = json.loads(r['machine'])
            observation = db.execute('SELECT payload FROM observations WHERE json_extract(payload,\'$.rfq_id\')=?', (r['source_id'],)).fetchone()
            if observation and not m.is_open(json.loads(observation[0]), moment):
                if machine.get('priority') != 'CLOSED':
                    counts['expired_results'] += 1
                machine.update(priority='CLOSED', source_status='Closed or expired',
                               next_action='Retained in archive. Confirm any reopening before responding.')
                db.execute('UPDATE results SET machine=? WHERE action_id=?', (m.dump(machine), r['action_id']))
        for r in db.execute("SELECT * FROM events WHERE status='pending'").fetchall():
            value = json.loads(r['payload'])
            latest = db.execute('SELECT revision FROM observations WHERE source_id=?', (r['source_id'],)).fetchone()
            stale = (latest and latest[0] != r['revision']) or not m.relevant(value, cfg)
            stale = stale or (r['kind'] != 'closed_or_expired' and not m.is_open(value, moment))
            old = (cutoff and datetime.fromisoformat(r['created_at']) < cutoff) or moment - datetime.fromisoformat(r['created_at']) > timedelta(hours=cfg.get('alert_max_age_hours', 24))
            if stale or old:
                db.execute("UPDATE events SET status=? WHERE event_id=?", ('superseded' if stale else 'suppressed_stale', r['event_id']))
                counts['suppressed_events'] += 1
    return counts


def legacy_actions(db, data_dir, moment):
    """Read through legacy decisions. Editing these cards writes back to actions.db."""
    import bdm_monitor as m
    path = data_dir / 'actions.db'
    if not path.exists():
        return 0
    rows = m.read_rows(path, 'SELECT * FROM actions')
    latest = max(((r.get('last_proposed') or '')[:10] for r in rows), default='')
    with db:
        for r in rows:
            archived = bool(latest and (r.get('last_proposed') or '')[:10] < latest)
            machine = dict(action_id='LEGACY:' + r['action_id'], source='Legacy actions', source_id=r['action_id'],
                           title=r['action_type'], organisation=r['organisation'],
                           priority='ARCHIVED' if archived else r['priority'], source_status='Historical proposal' if archived else 'Account action',
                           next_action=r.get('why_now') or '', evidence_detail=r.get('evidence') or '',
                           legacy_action_id=r['action_id'], retrieved_at=r.get('last_proposed') or '')
            stage = r['stage'] or 'identified'
            existing = db.execute('SELECT * FROM results WHERE action_id=?',(machine['action_id'],)).fetchone()
            human_values=(r.get('owner') or 'Sirish',stage,r['follow_up'],r['notes'],r['outcome'])
            if existing and existing['machine']==m.dump(machine) and tuple(existing[k] for k in m.HUMAN)==human_values:
                continue
            m.upsert_result(db, machine, r.get('owner') or 'Sirish', moment)
            db.execute('UPDATE results SET owner=?,stage=?,follow_up=?,notes=?,outcome=? WHERE action_id=?',
                       (*human_values, machine['action_id']))
    return len(rows)


def rows(db, moment):
    import bdm_monitor as m
    result = m.result_rows(db)
    today = moment.astimezone(m.ZoneInfo('Asia/Kolkata')).date().isoformat()
    for r in result:
        q = qualification(db, r['action_id'])
        closed = r['priority'] in ('CLOSED', 'ARCHIVED') or r['stage'] in ('won', 'lost', 'no fit') or q['decision'] == 'no pursue'
        r['qualification'] = q
        text = (r.get('title','') + ' ' + r.get('evidence_detail','')).lower()
        r['source_flags'] = []
        if any(s in text for s in ('selected suppliers only', 'not be extended to additional', 'not be extending this opportunity', 'closed for invites')):
            r['source_flags'].append('Supplier restriction stated: verify existing invitation')
        if any(s in text for s in ('must hold', 'current nv1', 'active baseline', 're-in statable')):
            r['source_flags'].append('Specific current-clearance wording: check full criteria')
        r['has_alert'] = bool(db.execute("SELECT 1 FROM events WHERE json_extract(payload,'$.rfq_id')=? AND status IN ('pending','sent') AND created_at>=? LIMIT 1",
                                        (r['source_id'], (moment-timedelta(hours=24)).isoformat())).fetchone())
        r['active'] = not closed
        r['overdue'] = not closed and any(d and d < today for d in (r['follow_up'], q['next_action_due']))
        r['awaiting_decision'] = not closed and q['decision'] == 'undecided'
        # Score uses verified human inputs; unknowns get no fit/access credit.
        score = (30 if r['priority'] == 'URGENT' else 10 if r['priority'] == 'PREPARE' else 0)
        score += 30 if q['capability'] == 'fit' else 10 if q['capability'] == 'partial' else 0
        score += 20 if q['access'] == 'invited' else 0
        score += 20 if q['readiness'] == 'ready' else 0
        r['qualification_score'] = score
        r['score_note'] = 'Timing + recorded capability/access/readiness; unknowns earn no fit credit'
    return sorted(result, key=lambda r: (not r['active'], not r['overdue'], -r['qualification_score'], r['deadline_canberra']))


def toast(title, message):
    """Request a Windows notification. Acceptance is not proof it was read."""
    if sys.platform != 'win32':
        return False
    import base64
    import html
    xml = '<toast><visual><binding template="ToastGeneric"><text>' + html.escape(title) + '</text><text>' + html.escape(message) + '</text></binding></visual></toast>'
    encoded = base64.b64encode(xml.encode('utf-8')).decode('ascii')
    script = '''[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String(''' + "'" + encoded + "'" + ''')))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Microsoft.WindowsPowerShell').Show($toast)
'''
    r = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden', '-Command', '-'],
                       input=script, text=True, capture_output=True, timeout=20)
    return r.returncode == 0


def desktop_alerts(db, cfg, moment):
    import bdm_monitor as m
    if not cfg.get('desktop_enabled'):
        return {'requested': 0}
    reconcile(db, cfg, moment)
    scan_cfg = dict(cfg, digest_limit=db.execute("SELECT count(*) FROM events WHERE status='pending'").fetchone()[0] + 1)
    available = [e for e in m.pending_events(db, scan_cfg, moment)
                 if not db.execute('SELECT 1 FROM desktop_receipts WHERE event_id=?', (e['event_id'],)).fetchone()][:cfg['digest_limit']]
    if not available:
        return {'requested': 0}
    # Persist before requesting a toast; a process crash must not flood duplicates.
    with db:
        db.executemany('INSERT INTO desktop_receipts VALUES(?,?,?)', [(e['event_id'], 'requesting', moment.isoformat()) for e in available])
    ids = ', '.join(json.loads(e['payload'])['rfq_id'] for e in available[:5])
    accepted = toast('Illoura: ' + str(len(available)) + ' BuyICT changes', ids + '. Open the Illoura BDM working view to review.')
    with db:
        db.executemany('UPDATE desktop_receipts SET status=? WHERE event_id=?', [('requested' if accepted else 'failed', e['event_id']) for e in available])
    return {'requested': len(available) if accepted else 0, 'accepted_by_windows': accepted}


def operational_alert(db, cfg, key, message, moment):
    setup(db)
    old = db.execute('SELECT last_notified FROM operational_notices WHERE notice_key=?', (key,)).fetchone()
    if old and moment - datetime.fromisoformat(old[0]) < timedelta(hours=1):
        return False
    if cfg.get('desktop_enabled') and toast('Illoura monitor needs attention', message):
        with db:
            db.execute('INSERT OR REPLACE INTO operational_notices VALUES(?,?)', (key, moment.isoformat()))
        return True
    return False


def due_reminders(db, cfg, moment):
    """One requested desktop reminder per local day; closed work is excluded."""
    import bdm_monitor as m
    if not cfg.get('desktop_enabled'):
        return 0
    today=moment.astimezone(m.ZoneInfo('Asia/Kolkata')).date().isoformat()
    key='followups:'+today
    if db.execute('SELECT 1 FROM operational_notices WHERE notice_key=?',(key,)).fetchone():
        return 0
    due=[r for r in rows(db,moment) if r['active'] and any(d and d<=today for d in (r['follow_up'],r['qualification']['next_action_due']))]
    if due and toast('Illoura: follow-ups due',str(len(due))+' work items need action today or are overdue. Review the working view.'):
        with db:db.execute('INSERT INTO operational_notices VALUES(?,?)',(key,moment.isoformat()))
        return len(due)
    return 0
