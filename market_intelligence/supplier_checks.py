"""Supplier identity checks: contract supplier ABNs against the national business registers.

Each finding is a research prompt, not an accusation. A cancelled ABN or a different
registered name often has an ordinary explanation: restructure, trustee, trading name.
Sole traders are counted and checked by ABN status only; no person's name is reported.
"""
from collections import Counter, defaultdict
from datetime import date, timedelta
from difflib import SequenceMatcher
import html
import json
from pathlib import Path
import re

from sqlalchemy import text

from .parsers import SourceError, name_key, valid_abn

STATUS_ORDER = ['invalid_contract_abn', 'register_not_loaded', 'outside_import_scope', 'abn_not_found',
                'abn_cancelled', 'name_differs', 'consistent']
STATUS_TEXT = {
    'invalid_contract_abn': 'The ABN published on the contract fails the ABN checksum.',
    'register_not_loaded': 'No ABN register snapshot is loaded yet.',
    'outside_import_scope': 'The ABN was not in the procurement scope when the register was imported; re-run the import.',
    'abn_not_found': 'The ABN is not in the public ABN extract (it may be suppressed or mistyped).',
    'abn_cancelled': 'The ABN is cancelled in the register.',
    'name_differs': 'No contract supplier name matches a registered name for this ABN.',
    'consistent': 'Active ABN and a contract name matches a registered name.'}
LEGAL_FORMS = [(r'\bproprietary\b', 'pty'), (r'\blimited\b', 'ltd'), (r'\bcorporation\b', 'corp'),
               (r'\bincorporated\b', 'inc'), (r'\bcompany\b', 'co'), (r'\baustralian\b', 'aust'), (r'\baustralia\b', 'aust')]
CORE_DROP = r'\b(pty|ltd|inc|corp|co|plc|llc|nl|aust|trust|unit|discretionary|family|trustee|trading)\b'
# "X Pty Ltd ATF Y Trust", "X T/A Y": each part is compared on its own.
NAME_PARTS = re.compile(r'\b(?:atf|as trustee for|trustee for|t/a|trading as)\b', re.I)


def legal_key(value):
    """Name key with legal forms written one way: 'Proprietary Limited' == 'Pty Ltd', "MEN'S" == 'MENS', 'M.G.S.' == 'MGS'."""
    value = re.sub(r"['’`]", '', value or '').replace('&', ' and ')
    key = name_key(value)
    for pattern, replacement in LEGAL_FORMS:
        key = re.sub(pattern, replacement, key)
    key = re.sub(r'^the trustee for (the )?', '', key)
    key = re.sub(r'^the ', '', key)
    merged, run = [], False
    for token in key.split():  # initials written with stops or spaces: "m g s" -> "mgs"
        single = len(token) == 1 and token.isalpha()
        if single and run:
            merged[-1] += token
        else:
            merged.append(token)
        run = single
    return ' '.join(merged)


def core_key(value):
    return ' '.join(re.sub(CORE_DROP, ' ', legal_key(value)).split())


def _parts(names):
    out = set()
    for name in names:
        if name:
            out.add(name)
            out.update(p for p in NAME_PARTS.split(name) if p and p.strip())
    return out


def _similar(contract_cores, register_cores):
    for c in contract_cores:
        if len(c) < 6:
            continue
        for r in register_cores:
            # AusTender cuts long supplier names short; a shorter label can also omit a suffix.
            if len(c) >= 10 and len(c.split()) >= 2 and r.startswith(c):
                return True
            if SequenceMatcher(None, c, r).ratio() >= 0.9:
                return True
    return False


def name_match(contract_names, main_name, other_names):
    names = _parts(contract_names)
    contract = {legal_key(n) for n in names}
    if not contract:
        return 'no_contract_name'
    if main_name and legal_key(main_name) in contract:
        return 'registered_name'
    others = {legal_key(n) for n in other_names if n}
    if contract & others:
        return 'other_registered_name'
    registered = [n for n in [main_name, *other_names] if n]
    cores = {core_key(n) for n in registered} - {''}
    contract_cores = {core_key(n) for n in names} - {''}
    if cores & contract_cores:
        return 'same_core_name'
    if _similar(contract_cores | contract, cores | {legal_key(n) for n in registered}):
        return 'similar_name'
    return 'differs' if registered else 'no_register_name'


def _abn_or_issue(value):
    try:
        return valid_abn(value), None
    except SourceError:
        return None, 'invalid'


def build_checks(engine, as_of):
    if not isinstance(as_of, date):
        raise ValueError('Explicit as-of date required')
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        contracts = [dict(r) for r in conn.execute(text('''
            SELECT c.cn_id,c.supplier,c.supplier_abn,c.buyer_key,c.first_published,c.original_value
            FROM market.current_analytical_contracts c''')).mappings()]
        procurement = conn.execute(text('''SELECT p.id,p.exported_at FROM market.procurement_snapshots p
            JOIN market.current_procurement_snapshot c ON c.snapshot_id=p.id''')).mappings().first()
        sources = {r['source']: dict(r) for r in conn.execute(text('''
            SELECT s.source,s.id,s.effective_date,s.effective_date_basis,s.retrieved_at,s.scope_kind,s.scope_count,
                   s.row_count,s.sha256,s.parser_revision,s.source_url,s.licence
            FROM market.register_snapshots s JOIN market.current_register_snapshots c ON c.snapshot_id=s.id''')).mappings()}
        suppliers, without_abn, invalid = defaultdict(list), [], defaultdict(list)
        for row in contracts:
            if not (row['supplier_abn'] or '').strip():
                without_abn.append(row)
                continue
            abn, problem = _abn_or_issue(row['supplier_abn'])
            (invalid[row['supplier_abn'].strip()] if problem else suppliers[abn]).append(row)
        abns = sorted(suppliers)
        entities = defaultdict(dict)
        for r in conn.execute(text('''SELECT source,record_id,abn,name,is_individual,status,status_from,registered_on,
                entity_type,attributes FROM market.current_register_entities WHERE abn = ANY(:abns)'''),
                {'abns': abns}).mappings():
            entities[r['source']].setdefault(r['abn'], []).append(dict(r))
        names = defaultdict(set)
        for r in conn.execute(text('''SELECT e.abn,n.name FROM market.register_names n
                JOIN market.current_register_entities e ON e.snapshot_id=n.snapshot_id AND e.record_id=n.record_id
                WHERE e.abn = ANY(:abns)'''), {'abns': abns}):
            names[r[0]].add(r[1])
        scope = set()
        if 'abn' in sources:
            scope = set(conn.execute(text('SELECT abn FROM market.register_scope WHERE snapshot_id=:s AND abn = ANY(:abns)'),
                                     {'s': sources['abn']['id'], 'abns': abns}).scalars())
    rows = [_supplier(abn, suppliers[abn], entities, names, scope, 'abn' in sources, as_of) for abn in abns]
    for value, items in invalid.items():
        rows.append(_invalid(value, items, as_of))
    rows.sort(key=lambda r: (STATUS_ORDER.index(r['status']), -r['contracts'], r['abn']))
    return dict(
        as_of=as_of, status='draft_for_review',
        procurement_snapshot_id=str(procurement['id']) if procurement else None,
        procurement_exported_at=procurement['exported_at'] if procurement else None,
        register_sources={k: {f: v for f, v in s.items() if f != 'id'} for k, s in sources.items()},
        summary=dict(Counter(r['status'] for r in rows)), flags=dict(Counter(f for r in rows for f in r['flags'])),
        suppliers_checked=len(rows),
        contracts_without_supplier_abn=dict(contracts=len(without_abn),
                                            distinct_supplier_labels=len({name_key(r['supplier']) for r in without_abn if r['supplier']})),
        limitations=[
            'Findings are prompts for review, not evidence of wrongdoing; restructures, trustees and trading names explain many.',
            'The ABN and ASIC registers were imported only for ABNs in the procurement snapshot at import time.',
            'Sole traders are checked by ABN status only; names are not compared or reported.',
            'ORIC registration identifies an Aboriginal and Torres Strait Islander corporation; it is not Supply Nation certification.'],
        suppliers=rows)


def _supplier(abn, items, entities, names, scope, abn_loaded, as_of):
    abr = (entities['abn'].get(abn) or [None])[0]
    oric = entities['oric'].get(abn) or []
    acnc = (entities['acnc'].get(abn) or [None])[0]
    asic = entities['asic_companies'].get(abn) or []
    business_names = entities['asic_business_names'].get(abn) or []
    individual = bool(abr and abr['is_individual'])
    published = sorted(r['first_published'] for r in items if r['first_published'])
    latest = published[-1] if published else None
    other = (set(names.get(abn, set())) | {e['name'] for e in [*oric, *asic, *business_names] if e['name']}
             | ({acnc['name']} if acnc and acnc['name'] else set()))
    flags = []
    if not abn_loaded:
        status = 'register_not_loaded'
    elif abr is None:
        status = 'abn_not_found' if abn in scope else 'outside_import_scope'
    elif abr['status'] == 'CAN':
        status = 'abn_cancelled'
        if latest and abr['status_from'] and abr['status_from'] < latest:
            flags.append('cancelled_before_latest_award')
    else:
        status = 'consistent'
    match = 'not_compared_sole_trader' if individual else name_match({r['supplier'] for r in items}, abr['name'] if abr else None, other)
    if status == 'consistent' and match in ('differs', 'no_register_name'):
        status = 'name_differs'
    if individual:
        flags.append('sole_trader')
    if any(e['status'] == 'Registered' for e in oric):
        flags.append('indigenous_corporation_oric')
    if acnc:
        flags.append('registered_charity')
    young = [e['registered_on'] for e in asic if e['registered_on'] and e['registered_on'] > as_of - timedelta(days=3*365)]
    if young:
        flags.append('company_registered_within_3_years')
    recent = sum(1 for d in published if d > as_of - timedelta(days=365) and d <= as_of)
    return dict(
        abn=abn, status=status, explanation=STATUS_TEXT[status], name_match=match, flags=flags,
        contract_supplier_names=[] if individual else sorted({r['supplier'] for r in items if r['supplier']}),
        registered_name=None if individual else (abr['name'] if abr else None),
        other_registered_names=[] if individual else sorted(other),
        abn_status=abr['status'] if abr else None, abn_status_from=abr['status_from'] if abr else None,
        entity_type=abr['entity_type'] if abr else None,
        asic_registered_on=min((e['registered_on'] for e in asic if e['registered_on']), default=None),
        oric=[dict(icn=e['record_id'], status=e['status'], registered_on=e['registered_on']) for e in oric],
        contracts=len(items), awards_last_365_days=recent, latest_award=latest,
        buyers=len({r['buyer_key'] for r in items}),
        original_value_total=float(sum(r['original_value'] for r in items if r['original_value'] is not None)),
        sample_contracts=sorted(r['cn_id'] for r in items)[:5])


def _invalid(value, items, as_of):
    published = sorted(r['first_published'] for r in items if r['first_published'])
    return dict(abn=value, status='invalid_contract_abn', explanation=STATUS_TEXT['invalid_contract_abn'],
                name_match='not_compared', flags=[], contract_supplier_names=sorted({r['supplier'] for r in items if r['supplier']}),
                registered_name=None, other_registered_names=[], abn_status=None, abn_status_from=None, entity_type=None,
                asic_registered_on=None, oric=[], contracts=len(items),
                awards_last_365_days=sum(1 for d in published if as_of - timedelta(days=365) < d <= as_of),
                latest_award=published[-1] if published else None, buyers=len({r['buyer_key'] for r in items}),
                original_value_total=float(sum(r['original_value'] for r in items if r['original_value'] is not None)),
                sample_contracts=sorted(r['cn_id'] for r in items)[:5])


def write_checks(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    raw = directory/'supplier_checks.json'
    page = directory/'supplier_checks.html'
    raw.write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
    esc = lambda v: html.escape(str(v if v is not None else 'Unknown'))
    look = [r for r in report['suppliers'] if r['status'] not in ('consistent', 'register_not_loaded')][:150]
    indigenous = sorted((r for r in report['suppliers'] if 'indigenous_corporation_oric' in r['flags']),
                        key=lambda r: (-r['contracts'], r['abn']))

    def label(r):
        return 'Sole trader (name not shown)' if 'sole_trader' in r['flags'] else '; '.join(r['contract_supplier_names']) or 'No name on contract'

    def card(r):
        return ('<article><h3>'+esc(label(r))+'</h3><p>'+esc(r['explanation'])+'</p><dl><dt>ABN</dt><dd>'+esc(r['abn'])
                +' · '+esc(r['abn_status'])+(' since '+esc(r['abn_status_from']) if r['abn_status_from'] else '')
                +'</dd><dt>Registered name</dt><dd>'+esc(r['registered_name'])+'</dd><dt>Contracts</dt><dd>'+esc(r['contracts'])
                +' · latest award '+esc(r['latest_award'])+' · '+esc(r['buyers'])+' buyers</dd><dt>Flags</dt><dd>'
                +esc(', '.join(f.replace('_', ' ') for f in r['flags']) or 'None')+'</dd></dl></article>')
    summary = ''.join('<tr><td>'+esc(k.replace('_', ' '))+'</td><td>'+esc(report['summary'].get(k, 0))+'</td></tr>'
                      for k in STATUS_ORDER if report['summary'].get(k))
    sources = ''.join('<li>'+esc(k)+': '+esc(v['effective_date'])+' ('+esc(v['scope_kind'].replace('_', ' '))+', '
                      +esc(v['row_count'])+' records)</li>' for k, v in sorted(report['register_sources'].items()))
    page.write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Supplier identity checks</title><style>body{font:16px/1.5 system-ui;max-width:1050px;margin:32px auto;padding:0 20px;'
        'color:#163144;background:#f4f6f8;overflow-wrap:anywhere}article{background:white;padding:16px 20px;margin:14px 0;'
        'border:1px solid #d9e0e5;border-radius:10px}dt{font-weight:600}dd{margin:0 0 8px}table{border-collapse:collapse;background:white}'
        'td{padding:6px 14px;border:1px solid #d9e0e5}</style><h1>Supplier identity checks</h1><p>Draft for internal review · As of '
        + esc(report['as_of'])+' · '+esc(report['suppliers_checked'])+' supplier ABNs</p><p>'+esc(' '.join(report['limitations']))
        + '</p><h2>Summary</h2><table>'+summary+'</table><p>Contracts without a supplier ABN: '
        + esc(report['contracts_without_supplier_abn']['contracts'])+'</p><h2>Register snapshots</h2><ul>'+(sources or '<li>None loaded</li>')
        + '</ul><h2>Worth a look ('+esc(len(look))+' shown)</h2>'+(''.join(card(r) for r in look) or '<p>Nothing flagged.</p>')
        + '<h2>Aboriginal and Torres Strait Islander corporations among suppliers ('+esc(len(indigenous))+')</h2>'
        + (''.join(card(r) for r in indigenous) or '<p>None found in the loaded registers.</p>')
        + '<p>Full detail: supplier_checks.json</p></html>', encoding='utf-8')
    return {'suppliers': report['suppliers_checked'], 'summary': report['summary'], 'json': str(raw), 'html': str(page)}
