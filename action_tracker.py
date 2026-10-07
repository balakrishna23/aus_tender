"""What happened to each action (spec engine 7, section 12): status, pipeline stage, outcome,
win/loss reason and follow-up date, kept in databases/actions.db.

People edit these in the "Tracker" tab of the Engine Views spreadsheet. Every run reads that tab
before anything else, stores the edits (with a history of every change), adds newly proposed
actions, and writes the tab back. TODAY then leaves out what is finished, declined or snoozed, and
brings back follow-ups that are due or overdue.

Each action keeps one stable id per action type and organisation, so a person's notes stay with it
from run to run while the evidence behind it is refreshed.
"""
import hashlib
import sqlite3
from datetime import date, datetime

from project_paths import database_directory

STATUSES = ['proposed', 'accepted', 'in progress', 'done', 'declined', 'snoozed']
STAGES = ['identified', 'contacted', 'meeting booked', 'proposal', 'won', 'lost', 'no fit']
CLOSED_STATUSES = {'done', 'declined'}
CLOSED_STAGES = {'won', 'lost', 'no fit'}
# Fields people own: a run never overwrites them.
HUMAN = ['owner', 'status', 'stage', 'follow_up', 'outcome', 'win_loss_reason', 'notes']
# Fields the engines refresh every run.
MACHINE = ['priority', 'score', 'why_now', 'person_and_route', 'offer', 'evidence', 'contact_safety', 'due']
TRACKER_COLUMNS = ['action_id', 'organisation', 'action_type', 'priority', 'score', 'due', 'owner', 'status',
                   'stage', 'follow_up', 'outcome', 'win_loss_reason', 'notes', 'why_now', 'evidence',
                   'first_proposed', 'updated_at']

SCHEMA = '''
CREATE TABLE IF NOT EXISTS actions (
    action_id TEXT PRIMARY KEY, organisation TEXT, action_type TEXT,
    priority TEXT, score INTEGER, why_now TEXT, person_and_route TEXT, offer TEXT, evidence TEXT,
    contact_safety TEXT, due TEXT,
    owner TEXT, status TEXT DEFAULT 'proposed', stage TEXT DEFAULT 'identified', follow_up TEXT,
    outcome TEXT, win_loss_reason TEXT, notes TEXT,
    first_proposed TEXT, last_proposed TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS action_history (
    action_id TEXT, field TEXT, old_value TEXT, new_value TEXT, changed_at TEXT
);
'''


def connect():
    path = database_directory() / 'actions.db'
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def action_id(action_type, organisation):
    digest = hashlib.sha1(f'{action_type}|{organisation}'.strip().lower().encode('utf-8')).hexdigest()
    return f'A-{digest[:8]}'


def _now():
    return datetime.now().isoformat(timespec='seconds')


def _clean(field, value):
    value = '' if value is None else str(value).strip()
    if field == 'status' and value.lower() not in STATUSES:
        return None  # unknown value: keep what was stored
    if field == 'stage' and value.lower() not in STAGES:
        return None
    if field in ('status', 'stage'):
        return value.lower()
    if field == 'follow_up' and value:
        # Sheets shows dates in the viewer's format: accept ISO, Australian and written forms.
        for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%d-%m-%Y', '%d %b %Y', '%d %B %Y', '%d-%b-%Y'):
            try:
                return datetime.strptime(value[:11].strip() if fmt == '%Y-%m-%d' else value, fmt).date().isoformat()
            except ValueError:
                continue
        return None
    return value or None


def record_edits(conn, sheet_rows):
    """Store people's edits from the Tracker tab. Returns how many fields changed."""
    changed = 0
    for row in sheet_rows:
        key = str(row.get('action_id') or '').strip()
        stored = conn.execute('SELECT * FROM actions WHERE action_id = ?', (key,)).fetchone()
        if not stored:
            continue
        for field in HUMAN:
            if field not in row:
                continue
            if field in ('status', 'stage') and not str(row[field] or '').strip():
                continue  # an emptied drop-down keeps the stored value
            new = _clean(field, row[field])
            if new is None and str(row[field] or '').strip():
                continue  # invalid value typed: ignore, keep the stored one
            if (new or None) != (stored[field] or None):
                conn.execute(f'UPDATE actions SET {field} = ?, updated_at = ? WHERE action_id = ?',
                             (new, _now(), key))
                conn.execute('INSERT INTO action_history VALUES (?, ?, ?, ?, ?)',
                             (key, field, stored[field], new, _now()))
                changed += 1
    conn.commit()
    return changed


def merge(conn, actions, today):
    """Add newly proposed actions; refresh the evidence of known ones (people's fields untouched)."""
    for a in actions:
        key = action_id(a['action_type'], a.get('subject') or a['organisation'])
        a['action_id'] = key
        if conn.execute('SELECT 1 FROM actions WHERE action_id = ?', (key,)).fetchone():
            conn.execute(f"UPDATE actions SET {', '.join(f'{m} = ?' for m in MACHINE)}, last_proposed = ? "
                         f"WHERE action_id = ?", [a.get(m) for m in MACHINE] + [today.isoformat(), key])
        else:
            conn.execute('INSERT INTO actions (action_id, organisation, action_type, owner, first_proposed, '
                         f"last_proposed, updated_at, {', '.join(MACHINE)}) VALUES "
                         f"(?, ?, ?, ?, ?, ?, ?, {', '.join('?' * len(MACHINE))})",
                         [key, a['organisation'], a['action_type'], a.get('owner'), today.isoformat(),
                          today.isoformat(), _now()] + [a.get(m) for m in MACHINE])
    conn.commit()


def is_closed(row):
    return (row['status'] or '') in CLOSED_STATUSES or (row['stage'] or '') in CLOSED_STAGES


def apply_to_today(conn, picked, today):
    """TODAY without finished, declined or snoozed actions, and with follow-ups that are due."""
    stored = {r['action_id']: r for r in conn.execute('SELECT * FROM actions')}
    kept = []
    for a in picked:
        row = stored.get(a.get('action_id'))
        if row and (is_closed(row) or (row['status'] == 'snoozed' and (row['follow_up'] or '') > today.isoformat())):
            continue
        if row:
            a = dict(a, owner=row['owner'] or a['owner'], status=row['status'], stage=row['stage'])
        kept.append(a)
    due = []
    for row in stored.values():
        if is_closed(row):
            continue
        follow_up = row['follow_up'] or (row['due'] if row['status'] in ('accepted', 'in progress') else None)
        if row['status'] in ('accepted', 'in progress', 'snoozed') and follow_up and follow_up <= today.isoformat():
            overdue = follow_up < today.isoformat()
            due.append(dict(
                rank=None, priority='OVERDUE' if overdue else 'FOLLOW UP', score=row['score'],
                organisation=row['organisation'], action_type=row['action_type'],
                why_now=f"Follow-up {'overdue since' if overdue else 'due'} {follow_up}"
                        f"{'; last note: ' + row['notes'] if row['notes'] else ''}",
                person_and_route=row['person_and_route'], offer=row['offer'], owner=row['owner'],
                due=follow_up, contact_safety=row['contact_safety'], evidence=row['evidence'],
                score_parts=None, assumptions=None, desired_outcome=None, status=row['status'],
                stage=row['stage'], action_id=row['action_id']))
    due.sort(key=lambda a: a['due'])
    due_ids = {a['action_id'] for a in due}
    result = due + [a for a in kept if a['action_id'] not in due_ids]
    for rank, a in enumerate(result, 1):
        a['rank'] = rank
    return result


def tracker_rows(conn):
    """Every action for the Tracker tab: follow-ups due first, then open ones by score, then closed."""
    rows = [dict(r) for r in conn.execute('SELECT * FROM actions')]
    today = date.today().isoformat()

    def order(r):
        closed = is_closed(r)
        due = bool(r['follow_up']) and r['follow_up'] <= today and not closed
        return (closed, not due, -(r['score'] or 0))
    return [{c: r.get(c) for c in TRACKER_COLUMNS} for r in sorted(rows, key=order)]
