#!/usr/bin/env python3
"""Autonomous outreach agent: emails the contacts on new people-related tenders.

Each run (scheduled every 15 minutes; sends only in Sydney business hours):
  1. Checks the off switch ('settings' tab of the Outreach sheet; fails safe to OFF).
  2. Reads the agent's inbox: "unsubscribe" -> do-not-contact, bounces -> invalid,
     real replies -> forwarded to OUTREACH_FORWARD_TO.
  3. Picks open, people-related releases with a contact email (same rules as outreach.py).
  4. Gathers the tender's facts and writes a personalised email with the AI writer,
     limited to those facts and templates/company_profile.yaml. Every draft is checked;
     a draft that fails falls back to the fixed template.
  5. Sends it with the signature and the capability statement attached, inside the
     sending window and daily cap, and records it (Outreach sheet, local log, activity log).

TEST MODE (OUTREACH_MODE=test, the default): every email goes to OUTREACH_TEST_RECIPIENT,
the subject shows who it would have gone to, and test sends never count against live ones.

Usage:
  python outreach_agent.py run [--now] [--dry-run] [--no-ai] [--max N]
  python outreach_agent.py inbox          # process replies only
  python outreach_agent.py summary        # email today's summary to OUTREACH_FORWARD_TO
"""
import argparse
from collections import Counter
import email
import html
import imaplib
import json
import logging
import os
import re
import smtplib
import sqlite3
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv

import activity_log
import outreach
from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('outreach_agent')

SYDNEY = ZoneInfo('Australia/Sydney')
TEMPLATES = PROJECT_DIR / 'templates'
PROFILE_PATH = TEMPLATES / 'company_profile.yaml'
ATTACHMENT_PATH = TEMPLATES / 'capability_statement.pdf'
SENT_COLUMNS = ['sent_at', 'mode', 'status', 'intended_to', 'sent_to', 'buyer', 'title', 'reference',
                'closes_at', 'writer', 'subject', 'body', 'url', 'release_key', 'note']
DNC_COLUMNS = ['email', 'reason', 'added_at', 'mode']
OPT_OUT = ('You are receiving this because your address is published as the contact for this '
           'opportunity. Reply "unsubscribe" and we will not contact you again.')
UNSUBSCRIBE_RE = re.compile(r'\b(unsubscribe|remove me|stop emailing|do not contact|opt[ -]?out)\b', re.I)
BOUNCE_FROM_RE = re.compile(r'mailer-daemon|postmaster', re.I)
WARMUP_DAYS, WARMUP_CAP = 14, 5


# Off by design: see run(). Only tests turn it on.
SENDING_ALLOWED = False


def env(name, default=''):
    return os.environ.get(name, default).strip().strip('"').strip("'")


def mode():
    return 'live' if env('OUTREACH_MODE', 'test').lower() == 'live' else 'test'


def sender():
    return env('OUTREACH_FROM_EMAIL')  # set in .env


def mail_password():
    # Gmail App Password; shown by Google in groups of four, spaces are not part of it.
    return (env('SMTP_PASSWORD') or env('gaccount_pwd')).replace(' ', '')


def forward_to():
    return env('OUTREACH_FORWARD_TO') or env('OUTREACH_TEST_RECIPIENT')


def load_profile(path=PROFILE_PATH):
    return yaml.safe_load(Path(path).read_text(encoding='utf-8'))


# ── Local state ───────────────────────────────────────────────────────────────
def state_db():
    conn = sqlite3.connect(database_directory() / 'outreach_agent.db', timeout=30)
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS sends (id INTEGER PRIMARY KEY, mode TEXT, release_key TEXT,
            intended_to TEXT, sent_to TEXT, subject TEXT, writer TEXT, message_id TEXT,
            sent_at TEXT, status TEXT, error TEXT);
        CREATE INDEX IF NOT EXISTS idx_sends_key ON sends(mode, release_key);
        CREATE TABLE IF NOT EXISTS suppressed (email TEXT, mode TEXT, reason TEXT, added_at TEXT,
            PRIMARY KEY (email, mode));
        CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, at TEXT, mode TEXT, kind TEXT,
            detail TEXT);
        CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT);
    ''')
    return conn


def now_text():
    return datetime.now().isoformat(sep=' ', timespec='seconds')


def record_event(conn, kind, detail):
    conn.execute('INSERT INTO events (at, mode, kind, detail) VALUES (?,?,?,?)', (now_text(), mode(), kind, detail))
    conn.commit()


def suppressed(conn, run_mode):
    """Live suppressions apply in both modes; test-mode unsubscribes only affect test runs."""
    return {r[0] for r in conn.execute('SELECT email FROM suppressed WHERE mode = ? OR mode = ?',
                                       (run_mode, 'live'))}


def suppress(conn, address, reason, run_mode):
    conn.execute('INSERT OR REPLACE INTO suppressed VALUES (?,?,?,?)',
                 (address.strip().lower(), run_mode, reason, now_text()))
    conn.commit()


# ── Guardrails ────────────────────────────────────────────────────────────────
def in_send_window(moment=None):
    """Weekdays 9am-4pm Sydney time, not on public holidays (national + OUTREACH_HOLIDAY_STATE)."""
    import holidays
    moment = (moment or datetime.now(SYDNEY)).astimezone(SYDNEY)
    region = holidays.AU(subdiv=env('OUTREACH_HOLIDAY_STATE', 'ACT'), years=moment.year)
    start, end = int(env('OUTREACH_SEND_FROM_HOUR', '9')), int(env('OUTREACH_SEND_TO_HOUR', '16'))
    if moment.weekday() >= 5:
        return False, 'weekend'
    if moment.date() in region:
        return False, f'public holiday ({region.get(moment.date())})'
    if not start <= moment.hour < end:
        return False, f'outside {start}:00-{end}:00 Sydney time'
    return True, ''


def first_send_key(run_mode):
    # Per mode: test sends (all to one inbox) must not use up the live warm-up.
    return f'first_send_day:{run_mode}'


def daily_cap(conn, today=None, run_mode=None):
    """Warm-up: WARMUP_CAP a day for the first WARMUP_DAYS days of sending, then the full cap."""
    today = today or date.today()
    started = conn.execute('SELECT value FROM state WHERE key = ?', (first_send_key(run_mode or mode()),)).fetchone()
    if not started or (today - date.fromisoformat(started[0])).days < WARMUP_DAYS:
        return WARMUP_CAP
    return int(env('OUTREACH_DAILY_CAP', '20'))


def pause_seconds(conn, today=None, run_mode=None):
    """Gap between emails: 30s while the mailbox warms up, 10s after (OUTREACH_DELAY_SECONDS overrides)."""
    if env('OUTREACH_DELAY_SECONDS'):
        return float(env('OUTREACH_DELAY_SECONDS'))
    return 30.0 if daily_cap(conn, today, run_mode) == WARMUP_CAP else 10.0


def sent_today(conn, run_mode, today=None):
    day = (today or date.today()).isoformat()
    return conn.execute("SELECT COUNT(*) FROM sends WHERE mode = ? AND status = 'sent' AND sent_at >= ?",
                        (run_mode, day)).fetchone()[0]


def events_today(conn, run_mode, kind='bounce'):
    return conn.execute('SELECT COUNT(*) FROM events WHERE mode = ? AND kind = ? AND at >= ?',
                        (run_mode, kind, date.today().isoformat())).fetchone()[0]


# ── Choosing who to email ─────────────────────────────────────────────────────
def candidates(conn, run_mode, releases=None, today=None):
    today = today or date.today()
    releases = releases if releases is not None else outreach.releases_from_db()
    done = {r[0] for r in conn.execute('SELECT release_key FROM sends WHERE mode = ?', (run_mode,))}
    chosen = outreach.select_candidates(releases, done, suppressed(conn, run_mode), today=today)
    cooldown = int(env('OUTREACH_COOLDOWN_DAYS', '14'))
    cutoff = (datetime.now() - timedelta(days=cooldown)).isoformat(sep=' ')
    recent = {r[0] for r in conn.execute("SELECT intended_to FROM sends WHERE mode = ? AND status = 'sent' "
                                         "AND sent_at >= ?", (run_mode, cutoff))}
    min_close = (today + timedelta(days=int(env('OUTREACH_MIN_DAYS_TO_CLOSE', '2')))).isoformat()
    picked, seen = [], set()
    for row in chosen:
        address = row['contact_email'].strip().lower()
        if address in recent or address in seen or (row.get('closes_on') or '9999') < min_close:
            continue
        seen.add(address)
        picked.append(row)
    return sorted(picked, key=lambda r: r.get('closes_on') or '9999')  # most urgent first


# ── Facts about one tender ────────────────────────────────────────────────────
def _one(db, sql, params):
    path = database_directory() / f'{db}.db'
    if not path.exists():
        return None
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


NAME_SOURCES = [  # where agencies publish a person's name next to a contact address
    ('aus_tender', 'tenders', 'contact_email', 'contact_name', 'publish_date'),
    ('aus_contract', 'contracts', 'email_address', 'contact_name', 'publish_date'),
    ('standing_offer', 'standing_offers', 'email_address', 'contact_name', 'publish_date'),
    ('planned_procurement', 'planned_procurements', 'contact_email', 'contact_name', 'last_updated'),
    ('nsw_tender', 'nsw_tenders', 'contact_email', 'contact_name', 'publish_date'),
]
STOP_WORDS = {'of', 'and', 'the', 'for', 'on', 'in', '&'}


def person_for_address(address, today=None, days=180, min_share=0.6, min_count=3):
    """The person who clearly owns a (possibly shared) mailbox, from names published next to
    this exact address in the last `days`; None when it is shared by several people."""
    from build_releases import parse_day
    address = (address or '').strip().lower()
    if not address:
        return None
    since = (today or date.today()) - timedelta(days=days)
    names = Counter()
    for db, table, email_col, name_col, date_col in NAME_SOURCES:
        path = database_directory() / f'{db}.db'
        if not path.exists():
            continue
        with closing(sqlite3.connect(path)) as conn:
            try:
                rows = conn.execute(f'SELECT {name_col}, {date_col} FROM {table} WHERE lower({email_col}) = ?',
                                    (address,)).fetchall()
            except sqlite3.OperationalError:
                continue
        for name, when in rows:
            day = parse_day(when or '')
            if name and (day is None or day >= since) and outreach.greeting_name(name) != 'there':
                names[' '.join(name.split()).title()] += 1
    if not names:
        return None
    (top, count), total = names.most_common(1)[0], sum(names.values())
    return top if count >= min_count and count / total >= min_share else None


def team_greeting(buyer, address):
    """'APRA Procurement team' from the buyer's initials and the mailbox name."""
    words = [w for w in re.split(r'[\s,]+', str(buyer or '')) if w and w.lower() not in STOP_WORDS]
    acronym = ''.join(w[0] for w in words).upper() if 2 <= len(words) <= 7 else ''
    local = (address or '').split('@')[0].lower()
    # A published tender address belongs to the procurement team whatever the mailbox is called
    # ("tenderresponses@" -> procurement team; "Tenders team" reads oddly).
    team = 'Contracts' if 'contract' in local and 'procure' not in local else 'Procurement'
    return ' '.join(x for x in (acronym, team, 'team') if x)


def greeting_for(row):
    """First name of the named contact, else the mailbox's clear owner, else the team."""
    name = outreach.greeting_name(row.get('contact_name'))
    if name != 'there':
        return name
    owner = person_for_address(row.get('contact_email'))
    if owner:
        # Full name: from other records we cannot be sure which part is the given name
        # (e.g. "Thi Anna Ly" goes by Anna), and a wrong first name is worse than none.
        return ' '.join(w for w in owner.split() if w.lower() not in outreach.TITLES)
    return team_greeting(row.get('buyer'), row.get('contact_email'))


def tender_facts(row):
    """Everything known about the tender, as short labelled facts for the writer."""
    facts = {
        'reference': row.get('source_id'), 'title': row.get('title'), 'buyer': row.get('buyer'),
        'portal': outreach.SOURCE_NAMES.get(row.get('source'), 'the tender portal'),
        'category': row.get('category'), 'location': row.get('location'),
        'closing': outreach._friendly_day(row.get('closes_on')) or row.get('closes_at'),
        'contact_name': row.get('contact_name'),
        'greeting_name': greeting_for(row),
    }
    if row.get('source') == 'buyict':
        extra = _one('buy_ict', 'SELECT requirements, criteria, rfq_type FROM buy_ict_tenders WHERE url = ?',
                     (row.get('url'),)) or {}
        for name in ('requirements', 'criteria'):
            try:
                value = json.loads(extra.get(name) or '{}')
            except ValueError:
                value = {}
            if isinstance(value, dict):
                for key, item in value.items():
                    if key not in ('all_criteria_labels',) and item not in (None, '', [], 'Not applicable'):
                        facts[f'{name}: {key}'] = item if not isinstance(item, str) else item[:600]
        facts['opportunity type'] = extra.get('rfq_type')
    elif row.get('source') == 'austender_atm':
        extra = _one('aus_tender', 'SELECT description, conditions_for_participation, atm_type FROM tenders '
                     'WHERE url = ?', (row.get('url'),)) or {}
        facts['description'] = (extra.get('description') or row.get('description') or '')[:900]
        facts['approach type'] = extra.get('atm_type')
    else:
        facts['description'] = (row.get('description') or '')[:900]
    return {k: v for k, v in facts.items() if v not in (None, '', [], {})}


PEOPLE_CATEGORY_WORDS = ('temporary personnel', 'recruit', 'staffing', 'personnel')
PLAN_ROLE_WORDS = ['advisors', 'advisers', 'consultants', 'analysts', 'developers', 'engineers', 'architects',
                   'testers', 'project managers', 'labour hire', 'temporary personnel', 'service desk', 'nurses',
                   'nursing', 'clinicians', 'support workers', 'secondment', 'resourcing', 'contract personnel',
                   'specialist personnel', 'ict specialists']
ICT_CATEGORY_WORDS = ('computer services', 'software', 'information technology', 'management advisory')


def _agency_name(name):
    return re.sub(r'\s+', ' ', str(name or '').replace('&', 'and')).strip().lower()


def _often(count):
    """Qualitative only: the writer never quotes counts (it reads as surveillance)."""
    return ('frequently' if count >= 25 else 'regularly' if count >= 8 else 'occasionally' if count >= 2
            else None)


def agency_context(buyer, today=None):
    """How the agency buys (last 12 months of AusTender contract notices and BuyICT requests) and
    its upcoming planned procurements related to what we supply. Empty when nothing is known."""
    from build_releases import parse_day
    today = today or date.today()
    since = today - timedelta(days=365)
    name = _agency_name(buyer)
    if not name:
        return {}
    contracts = [r for r in query_all('aus_contract', 'SELECT agency, category, publish_date FROM contracts')
                 if _agency_name(r['agency']) == name and (parse_day(r['publish_date']) or since) >= since]
    people = sum(1 for r in contracts if any(w in (r['category'] or '').lower() for w in PEOPLE_CATEGORY_WORDS))
    ict = sum(1 for r in contracts if any(w in (r['category'] or '').lower() for w in ICT_CATEGORY_WORDS))
    buyict = sum(1 for r in query_all('buy_ict', 'SELECT buyer, rfq_published_date FROM buy_ict_tenders')
                 if _agency_name(r['buyer']) == name and (parse_day(r['rfq_published_date']) or since) >= since)
    plans = query_all('planned_procurement', 'SELECT agency, title, category, estimated_date_of_approach_to_market, '
                                             'scraped_at FROM planned_procurements')
    latest = max((p['scraped_at'] or '' for p in plans), default='')[:10]
    # Plans for people we could supply, not for products ("Secure access system", "Records storage").
    words = PLAN_ROLE_WORDS + [w for w in outreach.keywords() if len(w) > 3]
    related = re.compile(r'\b(' + '|'.join(re.escape(w) for w in words) + ')', re.I)  # word start: not "biosecurity"
    upcoming = [{'title': p['title'], 'expected': p['estimated_date_of_approach_to_market']}
                for p in plans if _agency_name(p['agency']) == name and (p['scraped_at'] or '')[:10] >= latest
                and related.search(f"{p['title']} {p['category']}")
                and not _quarter_passed(p['estimated_date_of_approach_to_market'], today)][:3]
    context = {
        'engages contract personnel (temporary personnel services)': _often(people),
        'engages ICT and advisory services': _often(ict),
        'posts requests on BuyICT': _often(buyict),
        'upcoming planned procurements we could also support': upcoming,
    }
    return {k: v for k, v in context.items() if v}


def _quarter_passed(text, today):
    """'Quarter 2 2025/2026' (financial year from July) has ended before today."""
    m = re.search(r'Quarter\s*([1-4])\s*(\d{4})/(\d{4})', str(text or ''))
    if not m:
        return False
    quarter, first, second = int(m.group(1)), int(m.group(2)), int(m.group(3))
    end = {1: date(first, 9, 30), 2: date(first, 12, 31), 3: date(second, 3, 31), 4: date(second, 6, 30)}[quarter]
    return end < today


def query_all(db, sql):
    path = database_directory() / f'{db}.db'
    if not path.exists():
        return []
    with closing(sqlite3.connect(path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(sql)]
        except sqlite3.OperationalError:
            return []


# ── Writing ───────────────────────────────────────────────────────────────────
WRITER_RULES = """You write short, specific business-development emails from an Australian supplier
of contract personnel to the published contact on a government tender.

Write the body as four short paragraphs, in this order:
 1. "Hi {greeting_name}," then one or two sentences on what THIS tender asks for, as the
    tender's requirements: the role, the buyer and 2-3 concrete details from TENDER
    (seniority, clearance, location, working arrangement, start, duration).
 2. Our offer, starting exactly "We can put forward" and naming roles from SUPPLIER
    what_we_supply; you may add "who hold or can obtain <clearance from SUPPLIER>". Nothing
    else about our candidates: no availability, hours, start dates, or meeting requirements.
 3. Optional: one sentence from AGENCY (see below). Skip the paragraph if nothing fits.
 4. We will respond through the formal portal process; our capability statement is attached;
    one short closing sentence.

Rules:
- Use ONLY the facts in TENDER and SUPPLIER. Never invent candidates, numbers, years of
  experience, prices, past clients, certifications or outcomes.
- Offer only what SUPPLIER lists under what_we_supply that matches the tender. If nothing
  matches, offer the closest supplied area without overstating it.
- Attribute requirements to the tender ("the role requires NV1 clearance"). Never state that
  our candidates already meet a requirement, are available on a date, can start or commence,
  or that the company holds a clearance. Say we can put forward suitable candidates for the
  role; clearances belong to people ("candidates who hold or can obtain NV1"), never to us.
- AGENCY (optional context about how this buyer purchases): you may add ONE light sentence
  showing we understand how they work, in words only (e.g. "we know APRA regularly brings in
  specialist contractors"). Never quote counts, contract numbers or values.
- If AGENCY lists an upcoming planned procurement that is clearly for roles we supply, you may
  mention it in one sentence as something we could also help with later. If none is clearly
  relevant, do not mention plans at all.
- Make it clearly about THIS tender: name the role, the buyer and 2-3 concrete details from
  TENDER (e.g. clearance, seniority, location, working arrangement, start date, duration).
- Say we will respond through the formal portal process. Do not ask for a meeting or call.
- Mention that our capability statement is attached.
- Greet with "Hi {greeting_name}," exactly. Australian English. Polite, plain, no hype,
  no exclamation marks. 110-170 words in the body.
- No sign-off ("Kind regards", "Regards", "Sincerely"), signature, name, links, email
  addresses, phone numbers or unsubscribe text; those are added separately. End the body with
  one short closing sentence.
- Subject: under 90 characters, include the tender reference.

Reply with JSON only: {"subject": "...", "body": "..."}"""


class WriterError(Exception):
    pass


def ai_write(facts, profile, http=requests, agency=None, feedback=None):
    """Draft one email with the configured AI provider (Groq, OpenAI-compatible API).
    `feedback`: problems found in the previous draft, for one revision."""
    key = env('GROQ_API_KEY') or env('groq_api_key')
    if not key:
        raise WriterError('no groq_api_key in .env')
    supplier = {k: v for k, v in profile.items() if k != 'signature'}
    def compact(value):  # no indentation: fewer tokens per email
        return json.dumps(value, ensure_ascii=False, separators=(',', ':'))
    prompt = f'TENDER:\n{compact(facts)}\n\nSUPPLIER:\n{compact(supplier)}\n\nAGENCY:\n{compact(agency or {})}'
    if feedback:
        prompt += ('\n\nYour previous draft was rejected for: ' + '; '.join(feedback)
                   + '. Write a new draft that fixes these.')
    payload = {
        'model': env('OUTREACH_LLM_MODEL', 'openai/gpt-oss-120b'),
        'temperature': 0.5,
        'response_format': {'type': 'json_object'},
        # A short email needs little deliberation; "low" cuts the thinking tokens (and rate-limit use).
        'reasoning_effort': env('OUTREACH_LLM_REASONING', 'low'),
        'messages': [
            {'role': 'system', 'content': WRITER_RULES.replace('{greeting_name}', facts.get('greeting_name', 'there'))},
            {'role': 'user', 'content': prompt},
        ],
    }
    for attempt in range(4):
        response = http.post('https://api.groq.com/openai/v1/chat/completions',
                             headers={'Authorization': f'Bearer {key}'}, json=payload, timeout=90)
        if response.status_code != 429:
            break
        # Rate limit (tokens per minute): wait as long as the API asks, then try again.
        wait = retry_after(response.headers)
        log.info('AI writer rate-limited; waiting %.0fs (attempt %s)', wait, attempt + 1)
        time.sleep(wait)
    if response.status_code != 200:
        raise WriterError(f'AI writer HTTP {response.status_code}: {response.text[:200]}')
    try:
        draft = json.loads(response.json()['choices'][0]['message']['content'])
        return str(draft['subject']).strip(), strip_sign_off(str(draft['body']))
    except (KeyError, IndexError, ValueError, TypeError) as e:
        raise WriterError(f'AI writer returned an unusable reply: {e}') from e


def retry_after(headers):
    """Seconds to wait from Groq's headers ('retry-after: 7' or 'x-ratelimit-reset-tokens: 23.2s')."""
    for name in ('retry-after', 'x-ratelimit-reset-tokens'):
        m = re.match(r'(?:(\d+)m)?([\d.]+)s?$', str(headers.get(name) or '').strip())
        if m:
            return min(65.0, float(m.group(1) or 0) * 60 + float(m.group(2)) + 1)
    return 20.0


SIGN_OFF_RE = re.compile(r'^(kind regards|best regards|warm regards|regards|sincerely|yours (sincerely|faithfully)'
                         r'|cheers|best wishes)[,.!]?\s*$', re.I)


def strip_sign_off(body):
    """Drop trailing sign-off lines; the signature block adds its own. Also puts the greeting
    on its own line ("Hi APRA team, The role..." -> "Hi APRA team,\\n\\nThe role...")."""
    m = re.match(r'\s*(Hi [^,\n]{1,60},)[ \t]*(\S)', body)
    if m:
        body = f'{m.group(1)}\n\n{m.group(2).upper()}{body[m.end():]}'
    lines = body.strip().splitlines()
    while lines and (not lines[-1].strip() or SIGN_OFF_RE.match(lines[-1].strip())
                     or (len(lines) > 1 and SIGN_OFF_RE.match(lines[-2].strip()) and len(lines[-1].split()) <= 4)):
        lines.pop()
    return '\n'.join(lines).strip()


def check_draft(subject, body, facts, profile):
    """Problems with a draft (empty list = OK). Guards against invented or off-target content."""
    problems = []
    words = len(body.split())
    if not 60 <= words <= 260:
        problems.append(f'body is {words} words')
    if not subject or len(subject) > 120:
        problems.append('subject missing or too long')
    reference = str(facts.get('reference') or '')
    if reference and reference.lower() not in (subject + ' ' + body).lower():
        problems.append('tender reference not mentioned')
    if re.search(r'https?://|www\.|[\w.+-]+@[\w-]+\.', body):
        problems.append('contains a link or email address')
    if re.search(r'\[[^\]]*\]|\{[^}]*\}', subject + body):
        problems.append('contains a placeholder')
    if not body.lower().startswith(f"hi {facts.get('greeting_name', 'there')}".lower()):
        problems.append('wrong greeting')
    # Every number must come from the tender or the profile, e.g. no "15 years' experience".
    known = json.dumps(facts, ensure_ascii=False) + json.dumps(profile, ensure_ascii=False)
    for number in set(re.findall(r'\d[\d,.]*', subject + ' ' + body)):
        if number.strip('.,') not in known:
            problems.append(f'number {number!r} not in the tender or profile')
    if re.search(r'\$\s?\d|guarantee|best price|cheapest', body, re.I):
        problems.append('makes a pricing or guarantee claim')
    for pattern, what in PROMISE_PATTERNS:
        if re.search(pattern, body, re.I):
            problems.append(f'promises something we cannot know ({what})')
    return problems


# Claims a supplier cannot make before seeing candidates against the requirement.
PROMISE_PATTERNS = [
    (r'\b(available|ready|able) to (start|commence)\b', 'availability to start'),
    (r'\bcan (start|commence)\b', 'availability to start'),
    (r'\b(who|that|which) (meet|meets|satisfy|satisfies|match|matches) (the|all|your|these|those|its|this)\b',
     'candidates already meeting requirements'),
    (r'\b(meet|meets|satisfy|satisfies) (the|all|your|these|those) [^.]{0,40}(requirement|criteri)',
     'meeting the requirements'),
    (r'\b(illoura services|we|our company) (holds?|has|have) [^.]{0,40}clearance', 'the company holding a clearance'),
    (r'\bwe (currently )?have (a|an|several|many|\d+) [^.]{0,60}\bavailable\b', 'specific people available'),
    (r'\b(candidates?|personnel|consultants?|staff|professionals?) (will|would|can) [^.]{0,40}\b(meet|satisfy|work up to|'
     r'comply)\b', 'what our candidates will do'),
    (r'\bwill be able to\b', 'what our candidates will be able to do'),
    (r'\b(meet|meets|satisfy|satisfies)\b[^.]{0,30}\b(limit|hours|seniority|clearance)\b', 'meeting the requirements'),
]


def _join(items):
    return items[0] if len(items) == 1 else ', '.join(items[:-1]) + ' and ' + items[-1]


def template_write(row, profile, facts=None, agency=None):
    """The fallback, built by code from the same facts: personal, but never makes a claim."""
    facts = facts if facts is not None else tender_facts(row)
    agency = agency or {}
    role, _ = outreach.role_parts(row.get('title', ''))
    pick = lambda *keys: next((str(facts[k]) for k in keys if facts.get(k)), '')  # noqa: E731
    details = []
    level = pick('requirements: Experience level')
    if level:
        details.append(re.sub(r'\s+-\s+(.+)', r' (\1)', level) + ' level')
    clearance = pick('requirements: Security clearance')
    if clearance:
        m = re.match(r'(?i)must (?:be able to )?(obtain|hold|have)\s+(.*)', clearance)
        details.append(f'the ability to {m.group(1).lower()} {m.group(2)}' if m else f'{clearance} clearance')
    where, how = pick('requirements: Location of work', 'location'), pick('requirements: Working arrangements')
    if where:
        details.append(f'{how.lower()} work in {where}' if how else f'work in {where}')
    term = pick('requirements: Initial contract duration')
    if term:
        details.append(f'an initial term of {term}')
    first = (f"I saw that {row.get('buyer') or 'your agency'} has released \"{row.get('title')}\" "
             f"({row.get('source_id')}) on {facts.get('portal')}, closing {facts.get('closing') or 'soon'}.")
    if details:
        first += f' The role calls for {_join(details)}.'
    offer = (f"We can put forward suitable {outreach.specialty(row)} for this {role} role through "
             f"{profile.get('company', 'our company')}.")
    if profile.get('clearances'):
        offer += f" Our candidates hold or can obtain {_join(list(profile['clearances']))} clearance."
    paragraphs = [f"Hi {facts.get('greeting_name', 'there')},", first, offer]
    often = agency.get('engages contract personnel (temporary personnel services)')
    if often:
        paragraphs.append(f"We know {row.get('buyer')} {often} brings in specialist contractors, and we would "
                          f"welcome being considered for similar roles.")
    paragraphs.append(f"We will respond through the formal {facts.get('portal')} process. Our capability statement "
                      f"is attached for your reference.")
    subject = f"{role} ({row.get('source_id')}) - {profile.get('company', '')}".strip(' -')
    return subject, '\n\n'.join(paragraphs)


def write_email(row, profile, use_ai=True, ai=ai_write, attempts=2):
    """(subject, body, writer, note). A rejected AI draft gets one revision with the problems
    named; if that also fails (or the AI is unavailable) the template is used."""
    facts = tender_facts(row)
    agency = agency_context(row.get('buyer'))
    if use_ai:
        feedback, note = None, ''
        try:
            for attempt in range(attempts):
                subject, body = ai(facts, profile, agency=agency, feedback=feedback)
                problems = check_draft(subject, body, facts, profile)
                if not problems:
                    return subject, body, 'ai', f'revised once ({"; ".join(feedback)})' if feedback else ''
                feedback = problems
            note = 'AI draft rejected twice: ' + '; '.join(problems)
        except WriterError as e:
            note = str(e)
        log.warning('%s: %s - using the template', facts.get('reference'), note)
        subject, body = template_write(row, profile, facts, agency)
        return subject, body, 'template', note
    subject, body = template_write(row, profile, facts, agency)
    return subject, body, 'template', ''


# ── The message ───────────────────────────────────────────────────────────────
def signature_text(profile):
    s = profile.get('signature', {})
    lines = ['Kind regards,', s.get('name', ''), s.get('title', ''), profile.get('company', '')]
    if profile.get('abn'):
        lines[-1] += f" | ABN {profile['abn']}"
    lines.append(' | '.join(x for x in (s.get('phone'), s.get('email') or sender(), profile.get('website')) if x))
    return '\n'.join(line for line in lines if line)


def build_message(row, subject, body, profile, run_mode, attachment=ATTACHMENT_PATH):
    intended = row['contact_email'].strip()
    to = env('OUTREACH_TEST_RECIPIENT') if run_mode == 'test' else intended
    if run_mode == 'test' and not to:
        raise RuntimeError('OUTREACH_TEST_RECIPIENT is not set; refusing to send in test mode')
    msg = EmailMessage()
    msg['From'] = formataddr((profile.get('company') or profile.get('signature', {}).get('name', ''), sender()))
    msg['To'] = to
    msg['Subject'] = f'[TEST → {intended}] {subject}' if run_mode == 'test' else subject
    msg['Message-ID'] = make_msgid(domain=sender().split('@')[-1])
    msg['List-Unsubscribe'] = f'<mailto:{sender()}?subject=unsubscribe>'
    banner = (f'*** TEST MODE: in live mode this email would go to {intended} ***\n\n'
              if run_mode == 'test' else '')
    text = f'{banner}{body}\n\n{signature_text(profile)}\n\n{OPT_OUT}\n'
    msg.set_content(text)
    paragraphs = ''.join(f'<p>{html.escape(p).replace(chr(10), "<br>")}</p>' for p in body.split('\n\n'))
    banner_html = f'<p style="color:#b00"><b>{html.escape(banner.strip())}</b></p>' if banner else ''
    signature_html = html.escape(signature_text(profile)).replace('\n', '<br>')
    msg.add_alternative(
        f'<div style="font-family:Arial,sans-serif;font-size:14px;line-height:1.5">{banner_html}{paragraphs}'
        f'<p>{signature_html}</p><p style="color:#777;font-size:12px">{html.escape(OPT_OUT)}</p></div>',
        subtype='html')
    if attachment and Path(attachment).exists():
        msg.add_attachment(Path(attachment).read_bytes(), maintype='application', subtype='pdf',
                           filename=f"{profile.get('company', 'Capability')} - Capability Statement.pdf")
    return msg


def smtp_connection():
    if not mail_password():
        raise RuntimeError('No Gmail App Password: set gaccount_pwd in .env')
    server = smtplib.SMTP('smtp.gmail.com', 587, timeout=60)
    server.starttls()
    try:
        server.login(sender(), mail_password())
    except (smtplib.SMTPAuthenticationError, smtplib.SMTPServerDisconnected) as e:
        raise RuntimeError(f'Gmail login failed for {sender()}: use a 16-letter App Password '
                           f'(Google Account > Security > App passwords)') from e
    return server


# ── Sheet (switch + log) ──────────────────────────────────────────────────────
class AgentSheet:
    """The Outreach spreadsheet: 'settings' (off switch), 'sent' (log), 'do_not_contact'."""

    def __init__(self):
        import publish_sheets
        self.book = publish_sheets.client().open_by_key(publish_sheets.load_registry()['outreach'])

    def _tab(self, title, header, rows=200):
        import gspread
        try:
            return self.book.worksheet(title)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(title=title, rows=rows, cols=len(header))
            ws.append_row(header, value_input_option='RAW')
            return ws

    def sending_enabled(self):
        ws = self._tab('settings', ['setting', 'value', 'note'])
        rows = {r[0].strip().upper(): r for r in ws.get_all_values()[1:] if r}
        if 'SENDING' not in rows:
            ws.append_row(['SENDING', 'ON', 'Type OFF to stop the email agent immediately'], value_input_option='RAW')
            return True
        return str(rows['SENDING'][1]).strip().upper() == 'ON'

    def switch_off(self, reason):
        ws = self._tab('settings', ['setting', 'value', 'note'])
        for n, row in enumerate(ws.get_all_values(), start=1):
            if row and row[0].strip().upper() == 'SENDING':
                ws.update(f'B{n}:C{n}', [['OFF', f'Paused automatically {now_text()}: {reason}']])
                return

    def log_send(self, values):
        self._tab('sent', SENT_COLUMNS, rows=500).append_row([str(values.get(c, '')) for c in SENT_COLUMNS],
                                                              value_input_option='RAW')

    def do_not_contact(self, run_mode):
        ws = self._tab('do_not_contact', DNC_COLUMNS)
        return {str(r.get('email', '')).strip().lower() for r in ws.get_all_records()
                if r.get('email') and str(r.get('mode') or 'live') in (run_mode, 'live')}

    def add_do_not_contact(self, address, reason, run_mode):
        self._tab('do_not_contact', DNC_COLUMNS).append_row([address, reason, now_text(), run_mode],
                                                             value_input_option='RAW')


# ── Inbox ─────────────────────────────────────────────────────────────────────
def _text_of(message):
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_type() == 'text/plain' and not part.get_filename():
                return part.get_payload(decode=True).decode(part.get_content_charset() or 'utf-8', 'replace')
        return ''
    payload = message.get_payload(decode=True) or b''
    return payload.decode(message.get_content_charset() or 'utf-8', 'replace')


def classify(message):
    """('bounce' | 'unsubscribe' | 'reply', detail)."""
    from_addr = parseaddr(message.get('From', ''))[1]
    subject = message.get('Subject', '')
    text = _text_of(message)
    if BOUNCE_FROM_RE.search(from_addr) or re.search(r'undeliver|delivery status notification|failure notice',
                                                     subject, re.I):
        failed = message.get('X-Failed-Recipients') or ''
        match = re.search(r'Final-Recipient:\s*rfc822;\s*([^\s>]+)', str(message), re.I)
        return 'bounce', (failed or (match.group(1) if match else '')).strip()
    first_lines = '\n'.join(line for line in text.splitlines()[:8] if not line.startswith('>'))
    if UNSUBSCRIBE_RE.search(subject) or UNSUBSCRIBE_RE.search(first_lines):
        return 'unsubscribe', from_addr
    return 'reply', from_addr


def process_inbox(conn, sheet, smtp_factory=smtp_connection, imap_factory=None):
    """Handle unread mail: unsubscribes, bounces and replies. Returns counts."""
    counts = {'reply': 0, 'unsubscribe': 0, 'bounce': 0}
    imap = imap_factory() if imap_factory else imaplib.IMAP4_SSL('imap.gmail.com')
    if not imap_factory:
        imap.login(sender(), mail_password())
    try:
        imap.select('INBOX')
        since = (date.today() - timedelta(days=30)).strftime('%d-%b-%Y')
        _, ids = imap.search(None, f'(UNSEEN SINCE {since})')
        contacted = {r[0].lower() for r in conn.execute('SELECT intended_to FROM sends UNION SELECT sent_to FROM sends')
                     if r[0]}
        for msg_id in ids[0].split():
            _, data = imap.fetch(msg_id, '(BODY.PEEK[])')  # read without marking; only handled mail is marked
            message = email.message_from_bytes(data[0][1])
            kind, detail = classify(message)
            # Map a reply back to the email we sent, to find the agency it was meant for.
            refs = re.findall(r'<[^>]+>', f"{message.get('In-Reply-To', '')} {message.get('References', '')}")
            original = next((row for ref in refs for row in [conn.execute(
                'SELECT intended_to, release_key, mode, message_id FROM sends WHERE message_id = ?',
                (ref,)).fetchone()] if row), None)
            from_addr = parseaddr(message.get('From', ''))[1].lower()
            if kind != 'bounce' and original is None and from_addr not in contacted:
                continue  # not about our outreach (e.g. Google account notices): leave it unread
            intended = original[0] if original else detail
            run_mode = original[2] if original else mode()
            imap.store(msg_id, '+FLAGS', '\\Seen')
            counts[kind] += 1
            if kind == 'unsubscribe':
                suppress(conn, intended, 'replied unsubscribe', run_mode)
                if sheet:
                    sheet.add_do_not_contact(intended, 'replied unsubscribe', run_mode)
                activity_log.record_email(intended, 'unsubscribe', original[1] if original else '', 'unsubscribed')
            elif kind == 'bounce':
                target = intended or detail
                if target:
                    suppress(conn, target, 'bounced', run_mode)
                conn.execute("UPDATE sends SET status = 'bounced' WHERE intended_to = ? OR sent_to = ?",
                             (target, target))
                activity_log.record_email(target, 'bounce', '', 'bounced')
            else:
                forward(message, intended, smtp_factory)
                if original:
                    conn.execute("UPDATE sends SET status = 'replied' WHERE message_id = ?", (original[3],))
                activity_log.record_email(intended, message.get('Subject', ''), original[1] if original else '',
                                          'replied')
            record_event(conn, kind, f"{intended}: {' '.join(str(message.get('Subject', '')).split())[:120]}")
        conn.commit()
    finally:
        try:
            imap.logout()
        except Exception:
            pass
    return counts


def forward(message, intended, smtp_factory):
    target = forward_to()
    if not target:
        log.warning('Reply from %s not forwarded: OUTREACH_FORWARD_TO is not set', intended)
        return
    fwd = EmailMessage()
    fwd['From'] = sender()
    fwd['To'] = target
    subject = ' '.join(str(message.get('Subject', '')).split())  # folded headers carry line breaks
    fwd['Subject'] = f'Tender reply from {intended}: {subject}'[:200]
    fwd.set_content(f"A contact replied to the outreach agent.\n\nFrom: {message.get('From')}\n"
                    f"Date: {message.get('Date')}\nAbout: {intended}\n\n{_text_of(message)[:5000]}")
    server = smtp_factory()
    try:
        server.send_message(fwd)
    finally:
        server.quit()


# ── Run ───────────────────────────────────────────────────────────────────────
def run(now=False, dry_run=False, use_ai=True, max_emails=None, sheet=None, smtp_factory=smtp_connection,
        ai=ai_write, profile=None, releases=None, sleep=time.sleep):
    run_mode = mode()
    if not SENDING_ALLOWED and not dry_run:
        # Illoura spec, Week 0 item 1 and section 8: no autonomous emails, and never to a buyer
        # while their tender is live. The candidates here are open-tender buyers, so sending stays
        # off until outreach is rebuilt as drafts a person approves. Dry runs still write drafts.
        log.info('Autonomous sending is switched off (AI drafts, a person approves); nothing sent')
        return []
    profile = profile or load_profile()
    with closing(state_db()) as conn:
        if not dry_run:
            sheet = sheet or AgentSheet()
            if not sheet.sending_enabled():
                log.info('SENDING is OFF in the Outreach sheet; nothing sent')
                return []
        if not now and not dry_run:
            ok, why = in_send_window()
            if not ok:
                log.info('Not sending: %s', why)
                return []
        if events_today(conn, run_mode) >= int(env('OUTREACH_MAX_BOUNCES', '3')):
            log.warning('Too many bounces today; sending stays paused')
            return []
        if events_today(conn, run_mode, 'send_error') >= 2:
            log.warning('Gmail refused sends twice today (e.g. its daily limit); trying again tomorrow')
            return []
        cap = daily_cap(conn, run_mode=run_mode)
        room = max(0, cap - sent_today(conn, run_mode))
        if max_emails is not None:
            room = min(room, max_emails)
        blocked = sheet.do_not_contact(run_mode) if sheet else set()
        picks = [r for r in candidates(conn, run_mode, releases) if r['contact_email'].strip().lower() not in blocked]
        log.info('Mode %s: %s candidates, room for %s today (cap %s)', run_mode, len(picks), room, cap)
        results, server = [], None
        # Write every draft first, several at once (the AI call is the slow part), then send in order.
        batch = picks[:room]
        with ThreadPoolExecutor(max_workers=max(1, min(2, len(batch)))) as pool:  # free AI tier: 8k tokens/min
            drafts = list(pool.map(lambda r: write_email(r, profile, use_ai=use_ai, ai=ai), batch))
        pause = pause_seconds(conn, run_mode=run_mode)
        try:
            for row, (subject, body, writer, note) in zip(batch, drafts):
                msg = build_message(row, subject, body, profile, run_mode)
                result = {'release_key': outreach.release_key(row), 'intended_to': row['contact_email'].strip(),
                          'sent_to': msg['To'], 'subject': msg['Subject'], 'body': body, 'writer': writer,
                          'note': note, 'buyer': row.get('buyer'), 'title': row.get('title'),
                          'reference': row.get('source_id'), 'closes_at': row.get('closes_at'),
                          'url': row.get('url'), 'mode': run_mode}
                if dry_run:
                    results.append(dict(result, status='dry run'))
                    continue
                if server is None:
                    server = smtp_factory()  # connect once, only when there is something to send
                if results:
                    sleep(pause)
                try:
                    server.send_message(msg)
                    status, error = 'sent', ''
                except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused) as e:
                    status, error = 'failed', str(e)[:300]
                except smtplib.SMTPException as e:
                    # Gmail refused the whole message or dropped the connection (often its daily
                    # sending limit). Nothing is recorded, so these tenders are retried next run.
                    log.error('Gmail refused %s: %s; stopping this batch', result['reference'], e)
                    record_event(conn, 'send_error', f"{result['reference']}: {str(e)[:200]}")
                    if events_today(conn, run_mode, 'send_error') == 1:
                        try:
                            alert(f'The outreach agent could not send ({e}). It retries once more today, '
                                  f'then pauses until tomorrow.')
                        except Exception:
                            pass
                    break
                result.update(status=status, sent_at=now_text())
                conn.execute('INSERT INTO sends (mode, release_key, intended_to, sent_to, subject, writer, '
                             'message_id, sent_at, status, error) VALUES (?,?,?,?,?,?,?,?,?,?)',
                             (run_mode, result['release_key'], result['intended_to'], result['sent_to'],
                              result['subject'], writer, msg['Message-ID'], result['sent_at'], status, error))
                conn.execute('INSERT OR IGNORE INTO state VALUES (?, ?)',
                             (first_send_key(run_mode), date.today().isoformat()))
                conn.commit()
                try:  # the local database is the record; a Sheets hiccup must not stop the batch
                    sheet.log_send(result)
                except Exception as e:
                    log.warning('Sent, but not logged to the Outreach sheet: %s', e)
                activity_log.record_email(result['sent_to'], result['subject'], result['release_key'],
                                          f'{status} ({run_mode})', error)
                log.info('%s %s -> %s [%s writer]', status, result['reference'], result['sent_to'], writer)
                results.append(result)
        finally:
            if server is not None:
                try:
                    server.quit()
                except Exception:
                    pass
        return results


def run_with_inbox(**kwargs):
    """A scheduled run: replies first (so unsubscribes are honoured before sending), then sends."""
    with closing(state_db()) as conn:
        sheet = AgentSheet()
        try:
            counts = process_inbox(conn, sheet)
            log.info('Inbox: %s', counts)
        except Exception as e:
            log.error('Inbox check failed (sending continues): %s', e)
        if events_today(conn, mode()) >= int(env('OUTREACH_MAX_BOUNCES', '3')):
            sheet.switch_off('too many bounces today')
            alert(f'The outreach agent paused itself: {events_today(conn, mode())} bounces today. '
                  f'Check the do_not_contact tab, then set SENDING back to ON in the settings tab.')
            return []
    return run(sheet=sheet, **kwargs)


def alert(text):
    target = forward_to()
    if not target:
        return
    msg = EmailMessage()
    msg['From'], msg['To'], msg['Subject'] = sender(), target, 'Outreach agent alert'
    msg.set_content(text)
    server = smtp_connection()
    try:
        server.send_message(msg)
    finally:
        server.quit()


def summary(smtp_factory=smtp_connection):
    """Email today's activity to OUTREACH_FORWARD_TO."""
    day = date.today().isoformat()
    with closing(state_db()) as conn:
        sends = conn.execute('SELECT sent_at, mode, status, intended_to, subject, writer FROM sends '
                             'WHERE sent_at >= ? ORDER BY sent_at', (day,)).fetchall()
        events = conn.execute('SELECT at, kind, detail FROM events WHERE at >= ? ORDER BY at', (day,)).fetchall()
    lines = [f'Outreach agent - {day} (mode: {mode()})', '',
             f'Emails sent: {sum(1 for s in sends if s[2] == "sent")}']
    lines += [f'  {s[0][11:16]} {s[2]:7} {s[3]} - {s[4]} [{s[5]}]' for s in sends] or ['  none']
    lines += ['', 'Replies, unsubscribes and bounces:']
    lines += [f'  {e[0][11:16]} {e[1]}: {e[2]}' for e in events] or ['  none']
    text = '\n'.join(lines)
    target = forward_to()
    if target:
        msg = EmailMessage()
        msg['From'], msg['To'], msg['Subject'] = sender(), target, f'Outreach agent summary {day}'
        msg.set_content(text)
        server = smtp_factory()
        try:
            server.send_message(msg)
        finally:
            server.quit()
    return text


def scheduled():
    """The scheduled job (every 15 minutes): inbox always; sends only inside the window; after the window
    closes on a business day, today's summary once."""
    results = run_with_inbox()
    moment = datetime.now(SYDNEY)
    business_day = in_send_window(moment.replace(hour=10, minute=0))[0]
    with closing(state_db()) as conn:
        done = conn.execute("SELECT value FROM state WHERE key = 'summary_day'").fetchone()
        if business_day and moment.hour >= int(env('OUTREACH_SEND_TO_HOUR', '16')) and                 (not done or done[0] != moment.date().isoformat()):
            summary()
            conn.execute("INSERT OR REPLACE INTO state VALUES ('summary_day', ?)", (moment.date().isoformat(),))
            conn.commit()
            log.info('Daily summary sent to %s', forward_to())
    return results


def agent_lock():
    """Only one agent run at a time (scheduled task + a manual run must never double-send)."""
    path = PROJECT_DIR / 'logs' / '.outreach_agent.lock'
    path.parent.mkdir(exist_ok=True)
    handle = open(path, 'a+')
    try:
        if os.name == 'nt':
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def main(argv=None):
    load_dotenv(PROJECT_DIR / '.env')
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s')
    for stream in (sys.stdout, sys.stderr):  # logs may be redirected to a file
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Autonomous, personalised tender outreach')
    sub = parser.add_subparsers(dest='command', required=True)
    r = sub.add_parser('run', help='Check the inbox, then send to new matching tenders')
    r.add_argument('--now', action='store_true', help='Ignore the sending window (manual tests)')
    r.add_argument('--dry-run', action='store_true', help='Write and show the emails; send nothing')
    r.add_argument('--no-ai', action='store_true', help='Use the fixed template instead of the AI writer')
    r.add_argument('--max', type=int, help='Send at most this many this run')
    sub.add_parser('scheduled', help="Scheduled job: inbox, sends in the window, daily summary")
    sub.add_parser('inbox', help='Process replies, unsubscribes and bounces only')
    sub.add_parser('summary', help="Email today's summary")
    args = parser.parse_args(argv)
    lock = None if getattr(args, 'dry_run', False) else agent_lock()
    if lock is None and not getattr(args, 'dry_run', False):
        print('Another outreach agent run is in progress; skipping this one.')
        return 0
    if args.command == 'run':
        options = dict(now=args.now, use_ai=not args.no_ai, max_emails=args.max)
        results = run(dry_run=True, **options) if args.dry_run else run_with_inbox(**options)
        for result in results:
            print(f"\n=== {result['status']} | {result['writer']} | to {result['sent_to']} ===\n"
                  f"Subject: {result['subject']}\n\n{result['body']}"
                  + (f"\n[{result['note']}]" if result.get('note') else ''))
        print(f'\n{len(results)} email(s) {"previewed" if args.dry_run else "processed"} in {mode()} mode')
    elif args.command == 'scheduled':
        print(f'{len(scheduled())} email(s) sent in {mode()} mode')
    elif args.command == 'inbox':
        with closing(state_db()) as conn:
            print(process_inbox(conn, AgentSheet()))
    else:
        print(summary())
    try:  # keep the operations dashboard current; never fail the agent over it
        import dashboard
        dashboard.build()
    except Exception as e:
        log.warning('Dashboard not rebuilt: %s', e)
    return 0


if __name__ == '__main__':
    sys.exit(main())
