#!/usr/bin/env python3
"""Derived views for the Illoura engines, rebuilt from the source databases on every run.

Source data is never changed; everything here is derived and stored separately
(databases/intelligence.db), with a source link on every row (spec section 4).

  opportunities        tenders, BuyICT roles, plans, ACT and NSW notices: real status, ICT relevance,
                       role family, contact safety (spec 3.1, 3.3, section 8)          [engine 1]
  hot_list             planned ICT procurements expected this quarter or the next two   [engine 1]
  renewals             ICT and labour-hire contracts ending within a year, by window    [engine 2]
  exemption_use        Appendix A item 16 contracts by agency and supplier             [engine 4]
  agency_contacts      published procurement contacts: source, date, confidence, STALE, opt-outs [engine 6]
  buyict_links         BuyICT requests linked to the contracts that filled them, with an
                       estimated day rate (spec 3.6)                                    [engine 5]
  panel_members        current members of the watched panels, with ABNs and contract counts [engine 3]
  mmr_primes           NIAA MMR companies with their ABN and contract counts             [engine 3]
  mmr_candidates       their active contracts of $7.5m or more (candidates, never confirmed) [engine 3]
  nsw_small_contracts  NSW ICT contract awards of $250,000 or less                      [engine 4]
  organisations, organisation_names, organisation_review   one record per company (section 4)
  actions, today       proposed actions, scored, and the TODAY selection (today_list.py) [engine 7]

Usage:
  python build_intelligence.py
"""
import json
import logging
import re
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

import holidays

import classify
import action_tracker
import today_list
from project_paths import PROJECT_DIR, database_directory

log = logging.getLogger('build_intelligence')

BUYICT_ID = re.compile(r'\b(LH|PCS|RFI)-?(\d{4,6})\b')
LEGAL_WORDS = re.compile(r'\b(PTY|PROPRIETARY|LTD|LIMITED|INC|INCORPORATED|CORPORATION|CORP|THE)\b')
FY_QUARTER = re.compile(r'Quarter\s*([1-4])\s*(\d{4})\s*/\s*(\d{2,4})', re.I)


def source(name):
    """Read-only connection to a source database, or None when it doesn't exist yet."""
    path = database_directory() / f'{name}.db'
    if not path.exists():
        log.warning('No %s database yet; its views stay empty', name)
        return None
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def rows(name, sql, params=()):
    conn = source(name)
    if conn is None:
        return []
    with closing(conn):
        return [dict(r) for r in conn.execute(sql, params)]


def abn(value):
    digits = re.sub(r'\D', '', value or '')
    return digits if len(digits) == 11 else None


def name_key(name):
    """Spec section 4 step 2: upper case, no punctuation, PTY LTD = PTY LIMITED = LIMITED."""
    text = (name or '').upper().replace('&', ' AND ')
    text = re.sub(r'[^A-Z0-9 ]', ' ', text)
    return ' '.join(LEGAL_WORDS.sub(' ', text).split())


def unspsc_code(text):
    match = re.match(r'\s*(\d{8})', text or '')
    return match.group(1) if match else None


def fy_quarter_range(text):
    """'Quarter 3 2026/2027' -> (2027-01-01, 2027-03-31): Australian financial-year quarters."""
    match = FY_QUARTER.search(text or '')
    if not match:
        return None, None
    quarter, start_year = int(match.group(1)), int(match.group(2))
    month = (7, 10, 1, 4)[quarter - 1]
    year = start_year if quarter <= 2 else start_year + 1
    first = date(year, month, 1)
    following = date(year + (month + 3 > 12), (month + 2) % 12 + 1, 1)
    return first, following - timedelta(days=1)


def business_days(start, end, calendar=None):
    """Weekdays from start to end inclusive, less ACT public holidays."""
    if not start or not end or end < start:
        return 0
    calendar = calendar or holidays.Australia(subdiv='ACT', years=range(start.year, end.year + 1))
    days, day = 0, start
    while day <= end:
        days += day.weekday() < 5 and day not in calendar
        day += timedelta(days=1)
    return days


# ── Engine 1: opportunities ───────────────────────────────────────────────────
def opportunity(source_name, source_id, kind, title, buyer, category, closes, status_source, url, *,
                code=None, description='', published=None, contact=(None, None, None), location=None,
                source_is_ict=False, status_effective=None, safety=None, retrieved_at=None, expected=None):
    found = classify.classify(title, description, category, code, source_is_ict)
    status = status_effective or classify.effective_status(status_source, closes)
    closes_at = classify.closing_time(closes)
    expected_from, expected_to = expected or (None, None)
    return dict(source=source_name, source_id=source_id, kind=kind, title=title, buyer=buyer, category=category,
                unspsc=code, published=published, closes=closes,
                closes_at=closes_at.isoformat() if closes_at else None,
                expected_from=expected_from and expected_from.isoformat(),
                expected_to=expected_to and expected_to.isoformat(),
                status_source=status_source, status_effective=status, **found,
                contact_name=contact[0], contact_email=contact[1], contact_phone=contact[2],
                contact_safety=safety or classify.contact_safety(status), location=location, url=url,
                retrieved_at=retrieved_at)


def build_opportunities():
    out = []
    for r in rows('aus_tender', 'SELECT * FROM tenders'):
        out.append(opportunity(
            'AusTender', r.get('atm_id'), r.get('atm_type') or 'Approach to market', r.get('service_name'), r.get('agency'),
            r.get('category'), r.get('close_date_time'), 'Closed' if '/ShowClosed/' in (r.get('url') or '') else 'Open',
            r.get('url'), description=r.get('description'), published=r.get('publish_date'),
            contact=(r.get('contact_name'), r.get('contact_email'), r.get('contact_phone')), location=r.get('location'),
            retrieved_at=r.get('scraped_at')))
    for r in rows('buy_ict', 'SELECT * FROM buy_ict_tenders'):
        contact = r.get('buyer_contact') if '@' in (r.get('buyer_contact') or '') else None
        out.append(opportunity(
            'BuyICT', r.get('rfq_id'), r.get('rfq_type') or 'RFQ', r.get('title'), r.get('buyer'), None, r.get('rfq_closing_date'),
            r.get('status'), r.get('url'), description=r.get('description') or r.get('requirements') or '',
            published=r.get('rfq_published_date'), contact=(None, contact, None), location=r.get('location_of_work'),
            source_is_ict=True, retrieved_at=r.get('scraped_at')))
    for r in rows('act_tender', 'SELECT * FROM act_tenders'):
        out.append(opportunity(
            'Tenders ACT', r.get('reference'), r.get('category') or 'Tender', r.get('title'), r.get('agency'), r.get('category'),
            r.get('close_date'), r.get('status'), r.get('url'), description=r.get('description') or '',
            published=r.get('publish_date'), contact=(r.get('contact_name'), r.get('contact_email'), None),
            location='ACT', retrieved_at=r.get('scraped_at')))
    for r in rows('nsw_tender', 'SELECT * FROM nsw_tenders'):
        if (r.get('reference') or '').upper().startswith(('CAN-', 'SON-')) or (r.get('status') or '') == 'Contract award':
            continue  # contract awards and standing offers, not opportunities
        out.append(opportunity(
            'buy.nsw', r.get('reference'), r.get('procurement_type') or r.get('notice_type') or 'Tender', r.get('title'),
            r.get('agency'), r.get('category'), r.get('close_date'), r.get('status'), r.get('url'),
            description=r.get('description') or '', published=r.get('publish_date'),
            contact=(r.get('contact_name'), r.get('contact_email'), r.get('contact_phone')), location=r.get('location') or 'NSW',
            retrieved_at=r.get('scraped_at')))
    # Only plans still listed: every crawl re-stamps each listed item, so anything not re-seen within
    # a day of the newest stamp has been removed from its agency's plan (as publish_sheets does).
    for r in rows('planned_procurement', "SELECT * FROM planned_procurements WHERE scraped_at >= "
                                         "(SELECT datetime(MAX(scraped_at), '-1 day') FROM planned_procurements)"):
        expected = fy_quarter_range(r.get('estimated_date_of_approach_to_market'))
        out.append(opportunity(
            'AusTender plan', r.get('agency_reference') or r.get('url'), 'Planned procurement', r.get('title'), r.get('agency'),
            r.get('category'), None, r.get('status'), r.get('url'), code=unspsc_code(r.get('category')),
            contact=(r.get('contact_name'), r.get('contact_email'), r.get('contact_phone')), retrieved_at=r.get('scraped_at'),
            # Published so suppliers can prepare: registering interest is fine until the tender opens.
            status_effective='Planned', safety='SAFE_GENERAL', expected=expected))
    return out


def hot_list(opportunities, today):
    """Planned ICT procurements expected in the current or next two quarters, with a contact."""
    month = (today.month - 1) // 3 * 3 + 1
    window_start = date(today.year, month, 1)
    end_month = month + 9
    window_end = date(today.year + (end_month > 12), (end_month - 1) % 12 + 1, 1) - timedelta(days=1)
    return [o for o in opportunities
            if o['kind'] == 'Planned procurement' and o['is_relevant'] and o['expected_from']
            and window_start.isoformat() <= o['expected_from'] <= window_end.isoformat()
            and (o['contact_email'] or o['contact_phone'])]


# ── Engine 2: renewals ────────────────────────────────────────────────────────
def build_renewals(today, mmr_abns):
    windows = classify.config('scoring')['renewal_windows']
    horizon = (today + timedelta(days=max(w[1] for w in windows))).isoformat()
    out = []
    for r in rows('contract_api', 'SELECT * FROM contracts WHERE current_end_date BETWEEN ? AND ? '
                                  'AND category_family IS NOT NULL', (today.isoformat(), horizon)):
        days = (date.fromisoformat(r['current_end_date']) - today).days
        window = next((f'{lo}-{hi} days' for lo, hi in windows if lo <= days <= hi), None)
        out.append(dict(
            cn_id=r['cn_id'], window=window, days_left=days, current_end_date=r['current_end_date'],
            buyer=r['buyer'], supplier=r['supplier'], supplier_abn=r['supplier_abn'],
            supplier_on_mmr_list=int(abn(r['supplier_abn']) in mmr_abns), description=r['description'],
            category_family=r['category_family'], current_value=r['current_value'], start_date=r['start_date'],
            amendment_count=r['amendment_count'], contact_name=r['contact_name'],
            contact_email=r['contact_email'], contact_phone=r['contact_phone'], branch=r['contact_branch'],
            division=r['contact_division'], is_indigenous_exemption=r['is_indigenous_exemption'],
            contact_safety='SAFE_GENERAL', page_url=r['page_url'], api_url=r['source_url']))
    return out


# ── Engine 6: agency contacts ─────────────────────────────────────────────────
def opted_out():
    emails = {e.strip().lower() for e in classify.config('opt_outs').get('emails') or []}
    emails |= {r['email'].strip().lower() for r in rows('outreach_agent', "SELECT email FROM suppressed WHERE mode = 'live'")}
    return emails


def usable_phone(value):
    return len(re.sub(r'\D', '', value or '')) >= 8  # '00' and similar placeholders are not phone numbers


def optional_rows(name, sql, params=()):
    """rows() for tables an older or partial database may not have yet."""
    try:
        return rows(name, sql, params)
    except sqlite3.OperationalError as exc:
        log.warning('%s: %s', name, exc)
        return []


def build_agency_contacts(today, opportunities=()):
    """Published procurement contacts per agency (spec section 5): seed contacts (High confidence), the
    latest contract-notice contact per agency and address (Medium), and contacts named on AusTender
    tender notices, procurement plans and standing offer notices that contract notices lack (Medium).
    Blank or placeholder phones and branches are filled from the same address in later contract
    versions and those notices; 'enriched' says what was filled and from where.
    Every person has a source, a verified date and a confidence; STALE after 18 months; opt-outs are
    NO_CONTACT everywhere; an open tender's contact is FORMAL_CHANNEL_ONLY and not usable for outreach."""
    stale_after = classify.config('scoring')['stale_after_months'] * 30
    since = (today - timedelta(days=stale_after * 2)).isoformat()
    blocked = opted_out()
    out = []
    for c in classify.config('seed_contacts')['contacts']:
        out.append(dict(organisation=c['organisation'], name=None, email=c.get('email'), phone=c.get('phone'),
                        branch=None, source=f"Spec seed list: {c['source']}", verified='2026-10-01',
                        confidence='High', source_url=None))
    latest = {}
    for r in rows('contract_api', "SELECT buyer, contact_name, contact_email, contact_phone, contact_branch, "
                                  "first_published, page_url FROM contracts WHERE contact_email LIKE '%@%' "
                                  "AND first_published >= ?", ((today - timedelta(days=stale_after * 2)).isoformat(),)):
        key = (r['buyer'], r['contact_email'].strip().lower())
        if key not in latest or (r['first_published'] or '') > latest[key]['first_published']:
            latest[key] = r
    for (buyer, email), r in latest.items():
        out.append(dict(organisation=buyer, name=r['contact_name'], email=r['contact_email'].strip(),
                        phone=r['contact_phone'], branch=r['contact_branch'], source='AusTender contract notice',
                        verified=r['first_published'], confidence='Medium', source_url=r['page_url']))

    # Contacts on other AusTender notices: (organisation, email, name, phone, branch, date, safety, source, url).
    notices = []
    for o in opportunities:
        if o.get('source') not in ('AusTender', 'AusTender plan') or '@' not in (o.get('contact_email') or ''):
            continue
        when = o.get('published') if o['source'] == 'AusTender' else o.get('retrieved_at')
        moment = classify.closing_time(when) if o['source'] == 'AusTender' else None
        notices.append((o['buyer'], o['contact_email'], o.get('contact_name'), o.get('contact_phone'), None,
                        moment.date().isoformat() if moment else str(when or '')[:10] or None, o['contact_safety'],
                        'AusTender tender notice' if o['source'] == 'AusTender' else 'AusTender procurement plan', o.get('url')))
    for r in optional_rows('standing_offer', "SELECT agency, email_address, contact_name, contact_phone, publish_date, url "
                                             "FROM standing_offers WHERE email_address LIKE '%@%'"):
        moment = classify.closing_time(r['publish_date'])
        notices.append((r['agency'], r['email_address'], r['contact_name'], r['contact_phone'], None,
                        moment.date().isoformat() if moment else None, 'SAFE_GENERAL', 'AusTender standing offer notice', r['url']))
    # Later contract versions often carry the phone/branch the roll-up lacks.
    versions = [(None, r['contact_email'], r['contact_name'], r['contact_phone'], r['contact_branch'] or r['contact_division'],
                 (r['release_date'] or '')[:10], None, 'AusTender contract notice version', r['page_url'])
                for r in optional_rows('contract_api', "SELECT contact_email, contact_name, contact_phone, contact_branch, "
                                                       "contact_division, release_date, page_url FROM contract_versions "
                                                       "WHERE contact_email LIKE '%@%' AND release_date >= ?", (since,))]
    newest = sorted(notices + versions, key=lambda n: n[5] or '', reverse=True)
    phones, branches = {}, {}
    for n in newest:
        email = n[1].strip().lower()
        if usable_phone(n[3]):
            phones.setdefault(email, (n[3].strip(), n[7], n[5]))
        if (n[4] or '').strip():
            branches.setdefault(email, (n[4].strip(), n[7], n[5]))
    for c in out:
        email, filled = (c['email'] or '').strip().lower(), []
        if not usable_phone(c['phone']) and email in phones:
            c['phone'] = phones[email][0]
            filled.append(f'phone from {phones[email][1]} ({phones[email][2]})')
        if not (c['branch'] or '').strip() and email in branches:
            c['branch'] = branches[email][0]
            filled.append(f'branch from {branches[email][1]} ({branches[email][2]})')
        c['enriched'] = '; '.join(filled) or None
    # Agencies' own mail domains: gov.au, or seen at least 3 times as the agency contact on contract notices.
    # Tender notices sometimes name an outside adviser (an engineering or property firm) or a system address.
    agency_domains = {r['domain'] for r in optional_rows(
        'contract_api', "SELECT lower(substr(contact_email, instr(contact_email, '@') + 1)) AS domain FROM contracts "
                        "WHERE contact_email LIKE '%@%' GROUP BY 1 HAVING COUNT(*) >= 3")}
    def agency_own(email, organisation):
        domain = email.strip().lower().rsplit('@', 1)[-1].rstrip('.')
        if domain.endswith('gov.au') or domain in agency_domains:
            return True
        # Or the domain names the agency itself: airservicesaustralia.com, questacon.edu.au, abc.net.au (initials).
        label = domain.split('.')[0]
        words = re.findall(r'[a-z]+', (organisation or '').lower())
        initials = ''.join(w[0] for w in words if w not in ('of', 'the', 'and', 'for'))
        return len(label) >= 3 and (label in ''.join(words) or label == initials)
    # One row per address: agencies are named slightly differently across notice types.
    known = {(c['email'] or '').strip().lower() for c in out}
    for organisation, email, name, phone, branch, verified, safety, label, url in newest:
        key = email.strip().lower()
        if organisation is None or key in known or not verified or verified < since:
            continue
        known.add(key)
        own = agency_own(key, organisation)
        out.append(dict(organisation=organisation, name=name, email=email.strip(),
                        phone=phone if usable_phone(phone) else (phones.get(key) or (None,))[0],
                        branch=(branches.get(key) or (None,))[0],
                        source=label if own else f'{label} (contact outside the agency: adviser or system address)',
                        verified=verified, confidence='Medium' if own else 'Low', source_url=url, enriched=None,
                        notice_safety=safety))

    for c in out:
        age = (today - date.fromisoformat(c['verified'])).days if c['verified'] else None
        c['stale'] = int(age is not None and age > stale_after)
        c['opted_out'] = int((c['email'] or '').lower() in blocked)
        # A published procurement contact outside a live procurement: general introduction only.
        c['contact_safety'] = 'NO_CONTACT' if c['opted_out'] else c.pop('notice_safety', None) or 'SAFE_GENERAL'
        c.pop('notice_safety', None)
        c['usable_for_outreach'] = int(not c['opted_out'] and not c['stale'] and c['confidence'] in ('High', 'Medium')
                                       and c['contact_safety'] == 'SAFE_GENERAL')
    return out


# ── Engine 4: Indigenous pathways ─────────────────────────────────────────────
def build_exemption_use():
    return rows('contract_api', '''
        SELECT cn_id, buyer, supplier, supplier_abn, description, category_family, current_value,
               start_date, current_end_date, first_published, exemption, page_url, source_url AS api_url
        FROM contracts WHERE is_indigenous_exemption = 1 ORDER BY buyer, first_published DESC''')


def build_nsw_small_contracts():
    out = []
    for r in rows('nsw_tender', "SELECT * FROM nsw_tenders WHERE reference LIKE 'CAN-%'"):
        value = re.search(r'\$([\d,]+(?:\.\d+)?)', r['value'] or '')
        amount = float(value.group(1).replace(',', '')) if value else None
        found = classify.classify(r['title'], r['description'] or '', r['category'])
        if amount is not None and amount <= 250000 and found['is_relevant']:
            out.append(dict(reference=r['reference'], title=r['title'], agency=r['agency'], value=amount,
                            category=r['category'], relevance_reason=found['relevance_reason'],
                            published=r['publish_date'], url=r['url']))
    return out


# ── Engine 5: BuyICT roles -> contracts, estimated day rate ───────────────────
def build_buyict_links():
    roles = {r['rfq_id'].upper(): r for r in rows('buy_ict', 'SELECT rfq_id, title, buyer, url FROM buy_ict_tenders')
             if r['rfq_id']}
    calendar = holidays.Australia(subdiv='ACT', years=range(2015, date.today().year + 6))
    out, mentioned = [], set()
    for r in rows('contract_api', "SELECT * FROM contracts WHERE description LIKE '%LH%' "
                                  "OR description LIKE '%PCS%' OR description LIKE '%RFI%'"):
        for prefix, number in BUYICT_ID.findall(r['description'] or ''):
            ref = f'{prefix}-{number.zfill(5)}'
            mentioned.add(ref)
            role = roles.get(ref)
            if not role:
                continue
            start = date.fromisoformat(r['start_date']) if r['start_date'] else None
            end = date.fromisoformat(r['current_end_date']) if r['current_end_date'] else None
            days = business_days(start, end, calendar)
            out.append(dict(
                buyict_id=ref, role_title=role['title'], buyer=role['buyer'], cn_id=r['cn_id'],
                supplier=r['supplier'], supplier_abn=r['supplier_abn'], contract_value=r['current_value'],
                start_date=r['start_date'], end_date=r['current_end_date'], business_days=days,
                estimated_day_rate=round(r['current_value'] / days, 2) if days and r['current_value'] else None,
                rate_note='Estimate: contract value (usually incl. GST) / business days; not a quoted rate',
                buyict_url=role['url'], page_url=r['page_url']))
    rate = len({x['buyict_id'] for x in out}) / len(mentioned) if mentioned else 0
    log.info('BuyICT links: %s contracts for %s requests; %.0f%% of the %s IDs mentioned in contracts exist '
             'in BuyICT', len(out), len({x['buyict_id'] for x in out}), rate * 100, len(mentioned))
    return out, {'ids_in_contracts': len(mentioned), 'ids_found_in_buyict': len({x['buyict_id'] for x in out}),
                 'contracts_linked': len(out), 'buyict_requests': len(roles)}


# ── Engine 3: panel members, MMR primes ───────────────────────────────────────
def contract_counts(today):
    """ABN -> counts of contracts (all, active, ICT/labour hire, active $7.5m+)."""
    counts = defaultdict(Counter)
    for r in rows('contract_api', 'SELECT supplier_abn, category_family, current_value, current_end_date '
                                  'FROM contracts'):
        number = abn(r['supplier_abn'])
        if not number:
            continue
        active = (r['current_end_date'] or '') >= today.isoformat()
        c = counts[number]
        c['contracts'] += 1
        c['active'] += active
        c['it_contracts'] += r['category_family'] is not None
        c['active_it'] += active and r['category_family'] is not None
        c['active_over_7_5m'] += active and (r['current_value'] or 0) >= 7_500_000
    return counts


def build_panel_members(counts):
    watched = {p['son_id']: p['name'] for p in classify.config('panels')['panels']}
    if not watched:
        return []
    out = []
    marks = ','.join('?' * len(watched))
    for r in rows('standing_offer', f'SELECT son_id, title, url, current_suppliers FROM standing_offers '
                                    f'WHERE son_id IN ({marks})', tuple(watched)):
        # Contracts let under this panel: only notice pages carry the SON ID (aus_contract).
        panel_contracts = Counter(abn(x['supplier_abn']) for x in rows(
            'aus_contract', 'SELECT supplier_abn FROM contracts WHERE son_id = ?', (r['son_id'],)))
        for member in json.loads(r['current_suppliers'] or '[]'):
            number = abn(member.get('ABN'))
            c = counts.get(number, Counter())
            out.append(dict(son_id=r['son_id'], panel=watched[r['son_id']], member=member.get('Supplier Name'),
                            abn=number, state=member.get('State'), deed_start=member.get('Deed Start Date'),
                            deed_end=member.get('Deed End Date'), contracts=c['contracts'],
                            active_contracts=c['active'], it_contracts=c['it_contracts'],
                            panel_contracts=panel_contracts.get(number, 0), panel_url=r['url']))
    return out


def build_mmr(today, counts):
    """Spec engine 3 steps 1-3: NIAA list -> organisation (ABN) -> active contracts of $7.5m or more,
    labelled candidates (public data never says which contracts carry an MMR)."""
    listed = rows('organisations', '''
        SELECT l.organisation, l.contract_count AS mmr_contracts, l.list_date, m.supplier_abn, m.match_method,
               m.review FROM mmr_list l JOIN mmr_matches m ON m.organisation = l.organisation
        WHERE l.list_date = (SELECT MAX(list_date) FROM mmr_list) AND COALESCE(m.review, '') != 'rejected' ''')
    primes, candidates = [], []
    # Company groups (config): legal entities counted together, e.g. DXC's two listed entities.
    group_of = {name: group for group, names in (classify.config('organisation_aliases').get('groups') or {}).items()
                for name in names}
    group_mmr = Counter()
    for organisation, count in {(r['organisation'], r['mmr_contracts']) for r in listed}:
        group_mmr[group_of.get(organisation, organisation)] += count or 0
    by_abn = defaultdict(list)
    for r in listed:
        by_abn[r['supplier_abn']].append(r)
    for number, entries in by_abn.items():
        c = counts.get(number, Counter())
        for e in entries:
            group = group_of.get(e['organisation'], e['organisation'])
            primes.append(dict(organisation=e['organisation'], group_name=group, abn=number,
                               mmr_contracts=e['mmr_contracts'], group_mmr_contracts=group_mmr[group],
                               list_date=e['list_date'], match=e['match_method'],
                               match_review=e['review'] or 'unreviewed', contracts=c['contracts'],
                               active_contracts=c['active'], active_it_contracts=c['active_it'],
                               active_over_7_5m=c['active_over_7_5m']))
    marks = ','.join('?' * len(by_abn)) or "''"
    for r in rows('contract_api', f'''
            SELECT cn_id, supplier, supplier_abn, buyer, description, category_family, unspsc, current_value,
                   start_date, current_end_date, first_published, page_url, source_url
            FROM contracts WHERE supplier_abn IN ({marks}) AND current_end_date >= ? AND current_value >= 7500000
            ORDER BY current_value DESC''', (*by_abn, today.isoformat())):
        candidates.append(dict(r, status='candidate (public data does not confirm an MMR)',
                               it_contract=int(r['category_family'] is not None),
                               days_left=(date.fromisoformat(r['current_end_date']) - today).days))
    return primes, candidates


# ── Organisation master (spec section 4) ──────────────────────────────────────
def build_organisations():
    """One record per organisation: (1) same ABN; (2) normalised name; (3) alias table;
    (4) close names go to a review queue, never merged."""
    names = []   # (raw name, abn or None, kind, source)
    for r in rows('contract_api', 'SELECT supplier, supplier_abn, buyer, buyer_abn FROM contracts'):
        names.append((r['supplier'], abn(r['supplier_abn']), 'supplier', 'contract notices'))
        names.append((r['buyer'], abn(r['buyer_abn']), 'agency', 'contract notices'))
    for r in rows('standing_offer', 'SELECT current_suppliers FROM standing_offers'):
        for member in json.loads(r['current_suppliers'] or '[]'):
            names.append((member.get('Supplier Name'), abn(member.get('ABN')), 'supplier', 'panels'))
    for r in rows('organisations', 'SELECT organisation FROM mmr_list'):
        names.append((r['organisation'], None, 'supplier', 'NIAA MMR list'))
    for r in rows('buy_ict', 'SELECT DISTINCT buyer FROM buy_ict_tenders'):
        names.append((r['buyer'], None, 'agency', 'BuyICT'))
    aliases = {}
    for org in classify.config('organisation_aliases')['organisations']:
        for alias in org.get('aliases', []) + [org['canonical']]:
            aliases[name_key(alias)] = (org['canonical'], org.get('abn'))

    by_abn = defaultdict(Counter)
    kinds = defaultdict(Counter)
    for raw, number, kind, _ in names:
        if raw and raw.strip() and number:
            by_abn[number][raw.strip()] += 1
            kinds[number][kind] += 1
    orgs = {}                     # org_id -> record
    key_to_org = defaultdict(set)
    for number, counter in by_abn.items():
        org_id = f'ABN{number}'
        orgs[org_id] = dict(org_id=org_id, name=counter.most_common(1)[0][0], abn=number,
                            type=kinds[number].most_common(1)[0][0], names=len(counter))
        for raw in counter:
            key_to_org[name_key(raw)].add(org_id)
    for key, (canonical, number) in aliases.items():
        if number and f'ABN{number}' in orgs:
            key_to_org[key].add(f'ABN{number}')
            orgs[f'ABN{number}']['name'] = canonical

    links, review, seen = [], [], set()
    by_first_word = defaultdict(list)
    for key, ids in key_to_org.items():
        if key:
            by_first_word[key.split()[0]].append(key)
    for raw, number, kind, src in names:
        if not raw or not raw.strip() or (raw, src) in seen:
            continue
        seen.add((raw, src))
        key = name_key(raw)
        if number:
            links.append(dict(raw_name=raw, source=src, org_id=f'ABN{number}', method='ABN'))
            continue
        matches = key_to_org.get(key, set())
        if len(matches) == 1:
            links.append(dict(raw_name=raw, source=src, org_id=next(iter(matches)), method='normalised name'))
            continue
        if key in aliases:
            canonical, alias_abn = aliases[key]
            org_id = f'ABN{alias_abn}' if alias_abn else f'NAME:{name_key(canonical)}'
            orgs.setdefault(org_id, dict(org_id=org_id, name=canonical, abn=alias_abn, type=kind, names=1))
            links.append(dict(raw_name=raw, source=src, org_id=org_id, method='alias'))
            continue
        org_id = f'NAME:{key}'
        orgs.setdefault(org_id, dict(org_id=org_id, name=raw.strip(), abn=None, type=kind, names=1))
        links.append(dict(raw_name=raw, source=src, org_id=org_id,
                          method='ambiguous name' if len(matches) > 1 else 'name only'))
        candidates = [k for k in by_first_word.get(key.split()[0] if key else '', []) if k != key][:400]
        best = max(((SequenceMatcher(None, key, k).ratio(), k) for k in candidates), default=(0, None))
        if best[0] >= 0.88:
            for candidate in sorted(key_to_org[best[1]]):
                review.append(dict(raw_name=raw, source=src, candidate_org=candidate,
                                   candidate_name=orgs[candidate]['name'], similarity=round(best[0], 3),
                                   decision=None))
    return list(orgs.values()), links, review


# ── Storage ───────────────────────────────────────────────────────────────────
ACTION_COLUMNS = ['priority', 'score', 'organisation', 'action_type', 'why_now', 'person_and_route', 'offer',
                  'owner', 'due', 'contact_safety', 'evidence', 'score_parts', 'assumptions', 'status', 'stage',
                  'action_id']


def write(conn, table, records, columns=None):
    conn.execute(f'DROP TABLE IF EXISTS {table}')
    columns = columns or (list(records[0]) if records else ['empty'])
    conn.execute(f'CREATE TABLE {table} ({", ".join(columns)})')
    if records:
        conn.executemany(f'INSERT INTO {table} VALUES ({", ".join("?" * len(columns))})',
                         [[r.get(c) for c in columns] for r in records])
    log.info('%-20s %s rows', table, len(records))


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    (PROJECT_DIR / 'logs').mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
                        handlers=[logging.StreamHandler(sys.stdout),
                                  logging.FileHandler(PROJECT_DIR / 'logs' / 'build_intelligence.log', mode='a',
                                                      encoding='utf-8')])
    started, today = time.time(), date.today()
    mmr_abns = {r['supplier_abn'] for r in rows('organisations', 'SELECT supplier_abn FROM mmr_matches '
                                                                  "WHERE COALESCE(review, '') != 'rejected'")}
    opportunities = build_opportunities()
    links, link_stats = build_buyict_links()
    organisations, names, review = build_organisations()
    counts = contract_counts(today)
    primes, candidates = build_mmr(today, counts)
    renewals, exemption_use, hot = build_renewals(today, mmr_abns), build_exemption_use(), hot_list(opportunities, today)
    actions, today_rows = today_list.build(renewals, primes, exemption_use, hot, opportunities, today)
    # People's tracking (status, follow-ups) shapes TODAY: done or declined drop out, due follow-ups return.
    with closing(action_tracker.connect()) as tracker:
        action_tracker.merge(tracker, actions, today)
        today_rows = action_tracker.apply_to_today(tracker, today_rows, today)
        tracked = {r['action_id']: dict(r) for r in tracker.execute('SELECT * FROM actions')}
        for action in actions:
            action.update({field: tracked[action['action_id']][field] for field in ('owner', 'status', 'stage')})
    panels = build_panel_members(counts)
    import prime_briefs
    briefs = build_prime_briefs(actions, primes, candidates, renewals, panels, links)
    path = database_directory() / 'intelligence.db'
    partial = path.with_name(path.name + '.partial')
    partial.unlink(missing_ok=True)
    with closing(sqlite3.connect(partial)) as conn:
        write(conn, 'opportunities', opportunities)
        write(conn, 'hot_list', hot, list(opportunities[0]) if opportunities else None)
        write(conn, 'renewals', renewals)
        write(conn, 'exemption_use', exemption_use)
        write(conn, 'agency_contacts', build_agency_contacts(today, opportunities))
        write(conn, 'buyict_links', links)
        write(conn, 'panel_members', panels)
        write(conn, 'mmr_primes', primes)
        write(conn, 'mmr_candidates', candidates)
        write(conn, 'nsw_small_contracts', build_nsw_small_contracts(),
              ['reference', 'title', 'agency', 'value', 'category', 'relevance_reason', 'published', 'url'])
        write(conn, 'organisations', organisations)
        write(conn, 'organisation_names', names)
        write(conn, 'organisation_review', review,
              ['raw_name', 'source', 'candidate_org', 'candidate_name', 'similarity', 'decision'])
        write(conn, 'actions', actions, ACTION_COLUMNS)
        write(conn, 'today', today_rows, ['rank'] + ACTION_COLUMNS)
        write(conn, 'prime_briefs', briefs, prime_briefs.COLUMNS)
        write(conn, 'build_info', [dict(built_at=datetime.now().isoformat(timespec='seconds'),
                                        buyict_link_stats=json.dumps(link_stats))])
        conn.commit()
    partial.replace(path)
    # Test builds use isolated DB folders and must not replace production briefs.
    if path.parent == PROJECT_DIR / 'databases':
        prime_briefs.export(briefs, PROJECT_DIR)
    log.info('Built %s in %.0fs', path, time.time() - started)
    return 0


def build_prime_briefs(actions, primes, candidates, renewals, panels, links):
    import prime_briefs
    labour_counts = Counter()
    for row in rows('contract_api', "SELECT supplier_abn, cn_id FROM contracts "
                   "WHERE category_family = 'labour hire and recruitment' ORDER BY cn_id"):
        number = abn(row['supplier_abn'])
        if number:
            labour_counts[number] += 1
            # Representative source notices, not an unbounded list inside a Sheet cell.
            ids = labour_counts.setdefault(number + ':ids', [])
            if len(ids) < 3:
                ids.append(row['cn_id'])
    return prime_briefs.build(actions, primes, candidates, renewals, panels, links, labour_counts)


if __name__ == '__main__':
    sys.exit(main())
