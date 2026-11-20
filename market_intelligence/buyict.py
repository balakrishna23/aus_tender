"""BuyICT buyer universe (spec phase 2a): who buys through BuyICT arrangements, how much, from whom.

Built from contract notices that AusTender links to a BuyICT arrangement's standing offer ID.
Public data shows who won, never who was invited: invitation history stays 'unknown'.
"""
from collections import Counter, defaultdict
import csv
from datetime import date, timedelta
import html
import json
from pathlib import Path

from sqlalchemy import text

RULES = Path(__file__).parent/'rules'/'buyict_arrangements.json'
OUR_TAGS = ('ict_labour', 'ict_services', 'recruitment', 'labour_hire_general')
# AGOR 'Type of Body' values carry a letter prefix, e.g. 'A. Non-corporate Commonwealth entity'.
NON_CORPORATE_PATTERN = r'^([A-Z]\.\s*)?non-corporate commonwealth entit'


def load_arrangements(path=RULES):
    data = json.loads(Path(path).read_text(encoding='utf-8'))
    ids = [a['son_id'] for a in data['arrangements']]
    if len(ids) != len(set(ids)) or not all(i.startswith('SON') for i in ids):
        raise ValueError('BuyICT arrangement IDs must be unique standing offer IDs')
    return data


def _money(value):
    return float(value) if value is not None else 0.0


def build_buyict(engine, as_of, path=RULES):
    if not isinstance(as_of, date):
        raise ValueError('Explicit as-of date required')
    rules = load_arrangements(path)
    arrangements = {a['son_id']: a for a in rules['arrangements']}
    d12, d36 = as_of - timedelta(days=365), as_of - timedelta(days=1095)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        rows = [dict(r) for r in conn.execute(text('''
            SELECT DISTINCT c.cn_id, l.son_id, c.buyer_key, c.buyer, c.supplier, c.supplier_abn, c.original_value,
                   c.first_published, c.payload->>'category_tag' AS tag
            FROM market.current_analytical_contracts c
            JOIN market.procurement_panel_links l ON l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id
            WHERE l.son_id = ANY(:ids) AND c.first_published > :d36 AND c.first_published <= :asof'''),
            {'ids': list(arrangements), 'd36': d36, 'asof': as_of}).mappings()]
        identity = {r['buyer_key']: dict(r) for r in conn.execute(text('''
            SELECT r.buyer_key, r.match_status, r.review_status,
                   COALESCE(r.confirmed_organisation_id, r.organisation_id) AS organisation_id,
                   o.name AS register_name, a.payload->>'Type of Body' AS body_type, a.portfolio
            FROM market.buyer_resolution r
            LEFT JOIN market.organisations o ON o.organisation_id=COALESCE(r.confirmed_organisation_id, r.organisation_id)
            -- Body type and portfolio come from AGOR only; market.organisations usually shows the newer
            -- Government Directory row for the same body, so read the AGOR observation directly.
            LEFT JOIN market.organisation_observations a ON a.organisation_id=o.organisation_id AND a.source='agor' ''')).mappings()}
        potential = [dict(r) for r in conn.execute(text('''
            SELECT organisation_id, name, payload->>'Type of Body' AS body_type, portfolio FROM market.organisation_observations
            WHERE source='agor' AND payload->>'Type of Body' ~* :pattern
            ORDER BY name'''), {'pattern': NON_CORPORATE_PATTERN}).mappings()]
        snapshot = conn.execute(text('''SELECT p.id, p.exported_at FROM market.procurement_snapshots p
            JOIN market.current_procurement_snapshot c ON c.snapshot_id=p.id''')).mappings().first()
    buyers = defaultdict(list)
    for row in rows:
        buyers[row['buyer_key']].append(row)
    out = []
    for key, items in buyers.items():
        recent = [r for r in items if r['first_published'] > d12]
        relevant = [r for r in items if arrangements[r['son_id']]['relevant']]
        suppliers = Counter((r['supplier'] or 'Unknown', r['supplier_abn']) for r in relevant)
        per_cn = {r['cn_id'] for r in relevant}
        top = suppliers.most_common(5)
        ident = identity.get(key, {})
        by_arrangement = {}
        for r in items:
            a = by_arrangement.setdefault(arrangements[r['son_id']]['name'], dict(contracts_36m=0, value_36m=0.0, contracts_12m=0, value_12m=0.0))
            a['contracts_36m'] += 1
            a['value_36m'] += _money(r['original_value'])
            if r['first_published'] > d12:
                a['contracts_12m'] += 1
                a['value_12m'] += _money(r['original_value'])
        out.append(dict(
            buyer_key=key, buyer=items[0]['buyer'], organisation_id=ident.get('organisation_id'),
            identity_status=(ident.get('match_status') or 'unmatched') + ' / ' + (ident.get('review_status') or 'unreviewed'),
            body_type=ident.get('body_type'), portfolio=ident.get('portfolio'),
            contracts_12m=len({r['cn_id'] for r in recent}), value_12m=sum(_money(r['original_value']) for r in {r['cn_id']: r for r in recent}.values()),
            contracts_36m=len({r['cn_id'] for r in items}), value_36m=sum(_money(r['original_value']) for r in {r['cn_id']: r for r in items}.values()),
            relevant_contracts_36m=len(per_cn), relevant_value_36m=sum(_money(r['original_value']) for r in {r['cn_id']: r for r in relevant}.values()),
            by_arrangement=by_arrangement, categories=dict(Counter(r['tag'] or 'untagged' for r in items)),
            top_sellers=[dict(supplier=s, abn=a, contracts=n, share=round(n / len(relevant), 3)) for (s, a), n in top],
            top3_share=round(sum(n for _, n in suppliers.most_common(3)) / len(relevant), 3) if relevant else None,
            distinct_sellers=len(suppliers), last_award=max(r['first_published'] for r in items),
            invitation_history='unknown'))
    out.sort(key=lambda b: (-b['relevant_value_36m'], -b['relevant_contracts_36m'], b['buyer_key']))
    for rank, b in enumerate(out, 1):
        b['rank'] = rank
    seen = {b['organisation_id'] for b in out if b['organisation_id'] and b['relevant_contracts_36m']}
    found = Counter(r['son_id'] for r in rows)
    return dict(
        as_of=as_of, status='draft_for_review', procurement_snapshot_id=str(snapshot['id']) if snapshot else None,
        procurement_exported_at=snapshot['exported_at'] if snapshot else None, rules_retrieved=rules['retrieved'],
        sources=rules['sources'],
        arrangements=[dict(a, contracts_36m_linked=found.get(a['son_id'], 0)) for a in rules['arrangements']],
        buyers=out,
        potential_buyers_without_relevant_spend=[p for p in potential if p['organisation_id'] not in seen],
        summary=dict(buyers=len(out), buyers_with_relevant_spend=sum(1 for b in out if b['relevant_contracts_36m']),
                     relevant_value_36m=sum(b['relevant_value_36m'] for b in out),
                     non_corporate_entities=len(potential), non_corporate_without_relevant_spend=len(potential) - len(seen & {p['organisation_id'] for p in potential})),
        limitations=[
            'Only contract notices that AusTender links to a standing offer ID are counted; unlinked BuyICT purchases are missed.',
            'Values are original notice values, not payments; 12 and 36 month windows end on the as-of date.',
            'Public data shows who won work, not who was invited; invitation history is unknown until Illoura loads its own BuyICT records.',
            'Non-corporate Commonwealth entities must use coordinated arrangements (IPP guidelines, May 2026); other bodies may opt in.',
            'Buyer labels link to register bodies through machine proposals until a person approves them.'])


def write_buyict(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'buyict_buyers.json').write_text(json.dumps(report, default=str, indent=2), encoding='utf-8')
    with open(directory/'buyict_buyers.csv', 'w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['rank', 'buyer', 'body_type', 'portfolio', 'relevant_contracts_36m', 'relevant_value_36m',
                         'contracts_12m', 'value_12m', 'top3_share', 'distinct_sellers', 'top_sellers', 'last_award', 'invitation_history'])
        for b in report['buyers']:
            writer.writerow([b['rank'], b['buyer'], b['body_type'], b['portfolio'], b['relevant_contracts_36m'],
                             round(b['relevant_value_36m']), b['contracts_12m'], round(b['value_12m']), b['top3_share'],
                             b['distinct_sellers'], '; '.join(s['supplier'] for s in b['top_sellers']), b['last_award'], b['invitation_history']])
    esc = lambda v: html.escape(str(v if v is not None else 'Unknown'))
    money = lambda v: '${:,.0f}'.format(v)
    cards = []
    for b in report['buyers'][:40]:
        sellers = '; '.join(s['supplier'] + ' (' + str(s['contracts']) + ')' for s in b['top_sellers']) or 'None in our categories'
        routes = '; '.join(k + ': ' + str(v['contracts_36m']) for k, v in sorted(b['by_arrangement'].items()))
        cards.append('<article><h3>' + esc(b['rank']) + '. ' + esc(b['buyer']) + '</h3><dl><dt>Our categories, 36 months</dt><dd>'
                     + esc(b['relevant_contracts_36m']) + ' contracts, ' + money(b['relevant_value_36m']) + '</dd><dt>Last 12 months (all arrangements)</dt><dd>'
                     + esc(b['contracts_12m']) + ' contracts, ' + money(b['value_12m']) + '</dd><dt>Sellers that keep winning</dt><dd>' + esc(sellers)
                     + '</dd><dt>Top 3 sellers’ share</dt><dd>' + esc(b['top3_share']) + '</dd><dt>Arrangements used</dt><dd>' + esc(routes)
                     + '</dd><dt>Body type</dt><dd>' + esc(b['body_type']) + '</dd><dt>Invitation history</dt><dd>Unknown</dd></dl></article>')
    s = report['summary']
    (directory/'buyict_buyers.html').write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>BuyICT buyer universe</title><style>body{font:16px/1.5 system-ui;max-width:1050px;margin:32px auto;padding:0 20px;'
        'color:#163144;background:#f4f6f8;overflow-wrap:anywhere}article{background:white;padding:16px 20px;margin:14px 0;'
        'border:1px solid #d9e0e5;border-radius:10px}dt{font-weight:600}dd{margin:0 0 8px}</style><h1>BuyICT buyer universe</h1><p>Draft for internal review · As of '
        + esc(report['as_of']) + '</p><p>' + esc(s['buyers_with_relevant_spend']) + ' agencies bought ICT labour, recruitment or labour hire through these arrangements in 36 months ('
        + money(s['relevant_value_36m']) + '). ' + esc(s['non_corporate_without_relevant_spend']) + ' of ' + esc(s['non_corporate_entities'])
        + ' non-corporate Commonwealth entities show no such purchase in the linked notices.</p><p>' + esc(' '.join(report['limitations']))
        + '</p>' + ''.join(cards) + '<p>All buyers: buyict_buyers.csv; full detail: buyict_buyers.json</p></html>', encoding='utf-8')
    return {'buyers': len(report['buyers']), 'summary': report['summary'], 'json': str(directory/'buyict_buyers.json')}
