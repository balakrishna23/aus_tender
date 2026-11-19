"""Indigenous procurement levers (spec phase 2d) from contract facts and the official IPP rules.

Thresholds come only from rules/ipp_rules.json (each with its NIAA source); portfolio results from
rules/ipp_portfolio_results.csv (NIAA, data current at September 2025). Nothing here is a legal test:
a contract in the set-aside band is a signal to look at, not proof the set-aside applied.
"""
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
import html
import json
from pathlib import Path
import re

from sqlalchemy import text

from .buyict import load_arrangements
from .parsers import name_key

RULES_DIR = Path(__file__).parent/'rules'
OUR_TAGS = ['ict_labour', 'ict_services', 'recruitment', 'labour_hire_general', 'healthcare_staffing', 'nursing_agency']


def load_ipp_rules(path=RULES_DIR/'ipp_rules.json'):
    rules = json.loads(Path(path).read_text(encoding='utf-8'))
    msa, mmr = rules['mandatory_set_aside'], rules['mandatory_minimum_requirements']
    if not (0 < msa['min_value_gst_inclusive'] < msa['max_value_gst_inclusive'] < mmr['threshold_gst_inclusive']):
        raise ValueError('IPP thresholds are inconsistent')
    for section in [msa, mmr, rules['eligibility'], rules['exemption_16']]:
        if section['source'] not in rules['sources']:
            raise ValueError('Every IPP rule needs a listed source')
    if not all(re.fullmatch(r'[0-9]{2}', s) for s in mmr['segments']):
        raise ValueError('MMR segments must be two-digit UNSPSC segments')
    return rules


def load_portfolio_results(path=RULES_DIR/'ipp_portfolio_results.csv'):
    with open(path, newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    for r in rows:
        for k in ['volume_target', 'volume_actual', 'value_target', 'value_actual']:
            r[k] = int(r[k]) if r[k] else None
    return rows


def _portfolio_key(value):
    key = name_key((value or '').replace("'", ''))
    return re.sub(r'\b(portfolio|the)\b', ' ', key).split()


def build_levers(engine, as_of, rules_dir=RULES_DIR):
    if not isinstance(as_of, date):
        raise ValueError('Explicit as-of date required')
    rules = load_ipp_rules(Path(rules_dir)/'ipp_rules.json')
    results = load_portfolio_results(Path(rules_dir)/'ipp_portfolio_results.csv')
    coordinated = [a['son_id'] for a in load_arrangements(Path(rules_dir)/'buyict_arrangements.json')['arrangements']]
    msa, mmr = rules['mandatory_set_aside'], rules['mandatory_minimum_requirements']
    d12, d36 = as_of - timedelta(days=365), as_of - timedelta(days=1095)
    params = dict(asof=as_of, d12=d12, d36=d36, tags=OUR_TAGS, coordinated=coordinated, lo=msa['min_value_gst_inclusive'],
                  hi=msa['max_value_gst_inclusive'], mmr=mmr['threshold_gst_inclusive'], segments=list(mmr['segments']),
                  exempt=mmr['sub_category_exemptions'])
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        exemption16 = [dict(r) for r in conn.execute(text('''
            SELECT buyer_key, min(buyer) AS buyer, count(*) AS contracts, sum(original_value) AS value,
                   count(*) FILTER (WHERE payload->>'category_tag' = ANY(:tags)) AS our_category_contracts,
                   max(first_published) AS latest
            FROM market.current_analytical_contracts
            WHERE first_published > :d36 AND first_published <= :asof
              AND (payload->>'is_indigenous_exemption' IN ('1','true','True') OR payload->>'exemption' ILIKE 'App A: 16.%')
            GROUP BY buyer_key ORDER BY count(*) DESC'''), params).mappings()]
        set_aside_band = [dict(r) for r in conn.execute(text('''
            SELECT c.buyer_key, min(c.buyer) AS buyer, count(*) AS contracts, sum(c.original_value) AS value,
                   count(*) FILTER (WHERE c.payload->>'procurement_method'='Limited tender') AS limited_tender
            FROM market.current_analytical_contracts c
            WHERE c.first_published > :d12 AND c.first_published <= :asof AND c.payload->>'category_tag' = ANY(:tags)
              AND c.original_value BETWEEN :lo AND :hi
              AND NOT EXISTS (SELECT 1 FROM market.procurement_panel_links l
                              WHERE l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id AND l.son_id = ANY(:coordinated))
            GROUP BY c.buyer_key ORDER BY count(*) DESC'''), params).mappings()]
        high_value = [dict(r) for r in conn.execute(text('''
            SELECT c.cn_id, c.buyer, c.supplier, c.supplier_abn, greatest(coalesce(c.original_value,0), coalesce(c.current_value,0)) AS value,
                   c.current_end_date, c.payload->>'unspsc' AS unspsc, c.payload->>'category_tag' AS tag
            FROM market.current_analytical_contracts c
            WHERE greatest(coalesce(c.original_value,0), coalesce(c.current_value,0)) >= :mmr
              AND c.current_end_date >= :asof AND c.payload->>'category_tag' = ANY(:tags)
              AND left(coalesce(c.payload->>'unspsc',''),2) = ANY(:segments)
              AND NOT (coalesce(c.payload->>'unspsc','') = ANY(:exempt))'''), params).mappings()]
        tagged = conn.execute(text('''SELECT count(*) FILTER (WHERE payload ? 'category_tag') FROM market.current_contracts''')).scalar()
        portfolio_spend = [dict(r) for r in conn.execute(text('''
            SELECT o.portfolio, count(*) AS contracts, sum(c.original_value) AS value
            FROM market.current_analytical_contracts c
            JOIN market.buyer_resolution r ON r.buyer_key=c.buyer_key
            JOIN market.organisations o ON o.organisation_id=COALESCE(r.confirmed_organisation_id, r.organisation_id)
            WHERE c.first_published > :d12 AND c.first_published <= :asof AND c.payload->>'category_tag' = ANY(:tags)
              AND o.portfolio IS NOT NULL
            GROUP BY o.portfolio'''), params).mappings()]
    primes = defaultdict(lambda: dict(contracts=0, value=0.0, buyers=set(), latest_end=None, names=set()))
    for c in high_value:
        p = primes[c['supplier_abn'] or c['supplier']]
        p['contracts'] += 1
        p['value'] += float(c['value'])
        p['buyers'].add(c['buyer'])
        p['names'].add(c['supplier'])
        p['latest_end'] = max(filter(None, [p['latest_end'], c['current_end_date']]))
    prime_rows = sorted(({'supplier_abn': k if k and k.isdigit() else None, 'supplier': sorted(v['names'])[0],
                          'active_high_value_contracts': v['contracts'], 'value': v['value'], 'buyers': len(v['buyers']),
                          'latest_end_date': v['latest_end']} for k, v in primes.items()), key=lambda r: -r['value'])
    spend_by_key = {tuple(_portfolio_key(r['portfolio'])): r for r in portfolio_spend}
    portfolio_rows = []
    for r in results:
        if r['year'] != max(x['year'] for x in results if x['volume_actual'] is not None):
            continue
        spend = spend_by_key.get(tuple(_portfolio_key(r['portfolio'])))
        portfolio_rows.append(dict(r, volume_ratio=round(r['volume_actual'] / r['volume_target'], 2) if r['volume_target'] else None,
                                   value_ratio=round(r['value_actual'] / r['value_target'], 2) if r['value_target'] else None,
                                   our_category_contracts_12m=spend['contracts'] if spend else None,
                                   our_category_value_12m=float(spend['value']) if spend and spend['value'] is not None else None))
    portfolio_rows.sort(key=lambda r: (r['value_ratio'] if r['value_ratio'] is not None else 99))
    targets_next = [r for r in results if r['volume_actual'] is None]
    return dict(
        as_of=as_of, status='draft_for_review', rules_retrieved=rules['retrieved'], sources=rules['sources'],
        tagged_contracts_in_snapshot=tagged,
        exemption_16_by_buyer=exemption16, set_aside_band_by_buyer=set_aside_band, mmr_primes=prime_rows,
        portfolio_results=portfolio_rows, next_year_targets=targets_next,
        rules=dict(set_aside=msa, mmr={k: v for k, v in mmr.items() if k != 'segments'}, eligibility=rules['eligibility']),
        limitations=[
            'Set-aside band = our categories, original value between $80,000 and $200,000, not bought through a BuyICT coordinated arrangement; remote delivery and CPR exemptions are not checked.',
            'High-value primes = active contracts of $7.5 million or more in an MMR segment and our categories; whether MMR targets were written into each contract is not published.',
            'AusTender values are treated as GST inclusive (not yet confirmed).',
            'Portfolio spend uses machine-proposed buyer identities until people approve them.',
            'Category tags need the enriched procurement export; contracts exported before it count as untagged.'])


def write_levers(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'ipp_levers.json').write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
    esc = lambda v: html.escape(str(v if v is not None else 'Unknown'))
    money = lambda v: 'Unknown' if v is None else '${:,.0f}'.format(float(v))
    table = lambda head, rows: ('<table><tr>' + ''.join('<th>' + esc(h) + '</th>' for h in head) + '</tr>'
                                + ''.join('<tr>' + ''.join('<td>' + c + '</td>' for c in row) + '</tr>' for row in rows) + '</table>')
    e16 = table(['Buyer', 'Exemption 16 contracts (36 months)', 'Value', 'In our categories', 'Latest'],
                [[esc(r['buyer']), esc(r['contracts']), money(r['value']), esc(r['our_category_contracts']), esc(r['latest'])] for r in report['exemption_16_by_buyer'][:30]])
    msa = table(['Buyer', 'Contracts in the set-aside band (12 months)', 'Value', 'Of which limited tender'],
                [[esc(r['buyer']), esc(r['contracts']), money(r['value']), esc(r['limited_tender'])] for r in report['set_aside_band_by_buyer'][:30]])
    primes = table(['Prime', 'Active contracts ≥ $7.5m', 'Value', 'Buyers', 'Latest end'],
                   [[esc(r['supplier']), esc(r['active_high_value_contracts']), money(r['value']), esc(r['buyers']), esc(r['latest_end_date'])] for r in report['mmr_primes'][:30]])
    port = table(['Portfolio', 'Value target', 'Value achieved', 'Ratio', 'Our categories, 12 months'],
                 [[esc(r['portfolio']), money(r['value_target']), money(r['value_actual']), esc(r['value_ratio']), money(r['our_category_value_12m'])] for r in report['portfolio_results']])
    (directory/'ipp_levers.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Indigenous procurement levers</title><style>body{font:15px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:0 20px;color:#163144;'
        'background:#f4f6f8;overflow-wrap:anywhere}table{border-collapse:collapse;background:white;margin:12px 0;width:100%}th,td{padding:6px 10px;'
        'border:1px solid #d9e0e5;text-align:left;vertical-align:top}</style><h1>Indigenous procurement levers</h1><p>Draft for internal review · As of '
        + esc(report['as_of']) + ' · rules retrieved ' + esc(report['rules_retrieved']) + '</p><p>' + esc(' '.join(report['limitations']))
        + '</p><h2>Primes with high-value contracts (subcontracting targets)</h2>' + primes
        + '<h2>Agencies using Exemption 16 (direct to Indigenous SMEs)</h2>' + e16
        + '<h2>Agencies buying our categories in the set-aside band</h2>' + msa
        + '<h2>Portfolio results against targets (NIAA, latest published year)</h2>' + port
        + '<p>Full detail: ipp_levers.json</p></html>', encoding='utf-8')
    return {'primes': len(report['mmr_primes']), 'exemption_16_buyers': len(report['exemption_16_by_buyer']),
            'set_aside_band_buyers': len(report['set_aside_band_by_buyer']), 'json': str(directory/'ipp_levers.json')}
