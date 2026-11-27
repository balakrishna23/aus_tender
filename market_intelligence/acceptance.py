"""Phase sign-off packet: every "done criterion" of the 19 Nov 2026 build spec, measured from the database.

The spec says "Build and verify one phase at a time. Show me row counts and sample rows from each phase before
starting the next" and lists done criteria per phase. This report measures each criterion it can, marks the
ones only the owner can judge (for example "the top 25 look plausible to me") as needing owner review with the
rows to review, and never marks anything met that it cannot measure. It writes nothing to the database.
Statuses: met, not_met, owner_review (rows attached), not_measurable_here (with the reason).
"""
from datetime import timedelta
import html
import json
from pathlib import Path

from sqlalchemy import text

from .reporting import json_value

OUR_FAMILIES = ('ICT services', 'labour hire and recruitment')


def _exists(conn, name):
    return conn.execute(text('SELECT to_regclass(:n) IS NOT NULL'), dict(n=name)).scalar()


def _rows(conn, sql, **params):
    return [dict(r) for r in conn.execute(text(sql), params).mappings()]


def _ratio(numerator, denominator):
    return round(float(numerator) / float(denominator), 4) if denominator else None


def _criterion(phase, spec, status, measure=None, value=None, target=None, evidence=None, rows=None, note=None):
    return dict(phase=phase, spec=spec, status=status, measure=measure, value=value, target=target,
                evidence=evidence, note=note, rows=(rows or [])[:25])


def build_acceptance(engine, as_of):
    past = as_of - timedelta(days=365)
    items = []
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn, conn.begin():
        # ---------------------------------------------------------------- Phase 1
        abn = conn.execute(text('''SELECT s.scope_kind, s.metrics, s.row_count, s.effective_date FROM market.register_snapshots s
            JOIN market.current_register_snapshots c ON c.snapshot_id=s.id WHERE c.source='abn' ''')).mappings().first()
        if not abn:
            items.append(_criterion('1', 'At least 99% of active ABNs loaded', 'not_met', note='No ABN register loaded'))
        elif abn['scope_kind'] != 'national_active':
            items.append(_criterion('1', 'At least 99% of active ABNs loaded', 'not_met',
                                    measure='register scope', value=abn['scope_kind'], target='national_active',
                                    note='Only ABNs that appear in contracts are loaded; run import-register --national'))
        else:
            share = (abn['metrics'] or {}).get('active_abns_loaded_share')
            items.append(_criterion('1', 'At least 99% of active ABNs loaded', 'met' if share and share >= .99 else 'not_met',
                                    measure='active ABNs loaded / active ABNs in the extract', value=share, target=0.99,
                                    evidence=dict(rows=abn['row_count'], extract_date=abn['effective_date'])))
        asic = conn.execute(text('''SELECT count(*) AS companies, count(e.abn) AS with_abn,
            count(*) FILTER (WHERE e.abn IS NOT NULL AND EXISTS(SELECT 1 FROM market.current_register_entities a
                             WHERE a.source='abn' AND a.abn=e.abn)) AS matched
            FROM market.current_register_entities e WHERE e.source='asic_companies' ''')).mappings().one()
        share = _ratio(asic['matched'], asic['with_abn'])
        items.append(_criterion('1', 'ASIC-to-ABN match of at least 95% for companies',
                                'met' if share is not None and share >= .95 else 'not_met',
                                measure='companies whose recorded ABN is in the current ABN register / companies with an ABN',
                                value=share, target=0.95, evidence=dict(asic)))
        duplicates = conn.execute(text('''SELECT count(*) FROM (SELECT abn FROM market.current_register_entities
            WHERE source='abn' GROUP BY abn HAVING count(*)>1) d''')).scalar()
        shared = conn.execute(text('''SELECT count(*) FROM (SELECT abn FROM market.organisations WHERE abn IS NOT NULL
            GROUP BY abn HAVING count(*)>1) d''')).scalar()
        items.append(_criterion('1', 'The organisations table has no duplicate ABNs', 'met' if duplicates == 0 else 'not_met',
                                measure='duplicate ABNs in the ABN-keyed business register', value=duplicates, target=0,
                                note=f'{shared} ABNs are shared by several government bodies on purpose (a department and its '
                                     'units can publish one ABN); bodies are keyed by publisher ID so they are never merged.'))
        items.append(_criterion('1', 'Tests pass', 'not_measurable_here',
                                note='See the GitHub Actions run for the deployed commit (Ubuntu, Windows, PostgreSQL, API jobs).'))

        # ---------------------------------------------------------------- Phase 2
        value = conn.execute(text('''SELECT sum(c.original_value) AS total,
            sum(c.original_value) FILTER (WHERE c.supplier_abn IS NOT NULL AND EXISTS(SELECT 1 FROM market.current_register_entities a
                 WHERE a.source='abn' AND a.abn=c.supplier_abn)) AS supplier_matched,
            sum(c.original_value) FILTER (WHERE r.organisation_id IS NOT NULL AND r.review_status NOT IN ('rejected','needs_recheck'))
                 AS agency_proposed,
            sum(c.original_value) FILTER (WHERE r.review_status='approved' AND r.confirmed_organisation_id IS NOT NULL) AS agency_approved,
            sum(c.original_value) FILTER (WHERE c.supplier_abn IS NOT NULL AND r.review_status='approved'
                 AND r.confirmed_organisation_id IS NOT NULL AND EXISTS(SELECT 1 FROM market.current_register_entities a
                 WHERE a.source='abn' AND a.abn=c.supplier_abn)) AS both_approved
            FROM market.current_analytical_contracts c LEFT JOIN market.buyer_resolution r USING(buyer_key)
            WHERE c.first_published>:past AND c.first_published<=:asof'''), dict(past=past, asof=as_of)).mappings().one()
        share = _ratio(value['both_approved'] or 0, value['total'])
        items.append(_criterion('2', 'At least 95% of AusTender contract value matched to a supplier ABN and an AGOR agency',
                                'met' if share and share >= .95 else 'not_met',
                                measure='trailing-12-month original value with a registered supplier ABN and a person-approved agency',
                                value=share, target=0.95,
                                evidence=dict(supplier_abn_share=_ratio(value['supplier_matched'] or 0, value['total']),
                                              agency_proposed_share=_ratio(value['agency_proposed'] or 0, value['total']),
                                              agency_approved_share=_ratio(value['agency_approved'] or 0, value['total'])),
                                note='Machine-proposed agency matches count only after a person approves them (review page).'))
        from .watchlist import category_summary
        categories = category_summary(engine)
        other = categories['other_share_of_our_family_value']
        catch_all = categories['catch_all_share_of_our_family_value']
        # Our rules never tag contracts inside our two families as 'other', so the literal test passes trivially;
        # the honest measure of tagging quality is the share tagged only by the broad family (no keyword/UNSPSC).
        literal = other is not None and other < .2
        items.append(_criterion('2', 'Category tagging puts under 20% of our-category spend in other',
                                ('met' if catch_all is not None and catch_all < .5 else 'owner_review') if literal else 'not_met',
                                measure="share tagged 'other' (and share tagged only by broad family)",
                                value=dict(other=other, broad_family_only=catch_all), target='other < 0.20',
                                note=f'{catch_all} of our-category value is tagged only by its broad family (no keyword or UNSPSC '
                                     'evidence), so the specific tag (ICT labour vs ICT services, etc.) is a guess there; '
                                     'tune market_intelligence/rules/categories.json if that is too coarse.'))
        top = _rows(conn, '''SELECT buyer, sum(original_value) AS value_12m, count(*) AS contracts
            FROM market.current_analytical_contracts WHERE category_family=ANY(:f) AND first_published>:past AND first_published<=:asof
            GROUP BY buyer ORDER BY 2 DESC NULLS LAST LIMIT 25''', f=list(OUR_FAMILIES), past=past, asof=as_of)
        items.append(_criterion('2', 'The top 25 federal agencies by spend in our categories look plausible to me',
                                'owner_review', rows=top))

        # ---------------------------------------------------------------- Phase 2a
        from .buyict import load_arrangements
        rules = load_arrangements()
        missing = [a['son_id'] for a in rules['arrangements'] if not a.get('son_id') or not a.get('name')]
        items.append(_criterion('2a', 'Every BuyICT marketplace/panel ID identified with its source',
                                'met' if rules['arrangements'] and not missing and rules.get('sources') else 'not_met',
                                measure='arrangements with ID and name; sources recorded', value=len(rules['arrangements']),
                                evidence=dict(sources=rules.get('sources'), retrieved=rules.get('retrieved'), missing=missing)))
        if _exists(conn, 'market.v_buyict_buyers'):
            expected = conn.execute(text('''SELECT count(DISTINCT c.buyer_key) FROM market.current_analytical_contracts c
                JOIN market.procurement_panel_links l ON l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id
                WHERE l.son_id=ANY(:ids) AND c.first_published>:d36 AND c.first_published<=:asof'''),
                dict(ids=[a['son_id'] for a in rules['arrangements']], d36=as_of - timedelta(days=1095), asof=as_of)).scalar()
            seen = conn.execute(text('SELECT count(*) FROM market.v_buyict_buyers')).scalar()
            items.append(_criterion('2a', 'v_buyict_buyers covers every agency with BuyICT spend in our categories over 36 months',
                                    'met' if seen >= expected else 'not_met', measure='view rows / agencies with linked BuyICT spend',
                                    value=seen, target=expected))
        else:
            items.append(_criterion('2a', 'v_buyict_buyers covers every agency with BuyICT spend over 36 months', 'not_met',
                                    note='View not deployed yet (spec views migration)'))
        gaps = _rows(conn, 'SELECT * FROM market.v_buyict_gaps LIMIT 25') if _exists(conn, 'market.v_buyict_gaps') else []
        items.append(_criterion('2a', 'v_buyict_gaps top 25 reviewed by me', 'owner_review' if gaps else 'not_met', rows=gaps,
                                note=None if gaps else 'View not deployed yet'))

        # ---------------------------------------------------------------- Phase 2b
        top50 = _rows(conn, '''SELECT COALESCE(r.confirmed_organisation_id,r.organisation_id) AS organisation_id, min(c.buyer) AS buyer,
            sum(c.original_value) AS value FROM market.current_analytical_contracts c JOIN market.buyer_resolution r USING(buyer_key)
            WHERE c.category_family=ANY(:f) AND c.first_published>:past AND c.first_published<=:asof
              AND COALESCE(r.confirmed_organisation_id,r.organisation_id) IS NOT NULL
            GROUP BY 1 ORDER BY 3 DESC NULLS LAST LIMIT 50''', f=list(OUR_FAMILIES), past=past, asof=as_of)
        if _exists(conn, 'market.v_agency_org_map'):
            mapped = {r[0] for r in conn.execute(text('SELECT DISTINCT agency_id FROM market.v_agency_org_map'))}
            covered = [t for t in top50 if t['organisation_id'] in mapped]
            share = _ratio(len(covered), len(top50))
            items.append(_criterion('2b', 'Government Directory units mapped for the top 50 agencies',
                                    'met' if top50 and len(covered) == len(top50) else 'not_met',
                                    measure='top-50 agencies (by spend in our categories) with at least one mapped role holder',
                                    value=share, target=1.0, rows=[t for t in top50 if t['organisation_id'] not in mapped]))
        else:
            items.append(_criterion('2b', 'Government Directory units mapped for the top 50 agencies', 'not_met',
                                    note='Role holders not loaded yet (migration 024)'))
        links = conn.execute(text('''SELECT count(DISTINCT l.cn_id) AS contracts,
            count(DISTINCT l.cn_id) FILTER (WHERE p.son_id IS NOT NULL) AS with_notice
            FROM market.procurement_panel_links l JOIN market.current_procurement_snapshot c ON c.snapshot_id=l.snapshot_id
            LEFT JOIN market.procurement_panels p ON p.snapshot_id=l.snapshot_id AND p.son_id=l.son_id''')).mappings().one()
        share = _ratio(links['with_notice'], links['contracts'])
        items.append(_criterion('2b', 'Panel notices linked to contracts for at least 80% of panel-ID contracts',
                                'met' if share is not None and share >= .8 else 'not_met',
                                measure='panel-ID contracts whose standing offer notice is loaded', value=share, target=0.8,
                                evidence=dict(links)))

        # ---------------------------------------------------------------- Phase 2c
        panels = conn.execute(text('''SELECT count(*) AS candidates, count(marked_relevant) AS marked,
            count(*) FILTER (WHERE marked_relevant) AS relevant,
            count(*) FILTER (WHERE marked_relevant AND NOT ((current_expiry IS NOT NULL AND illoura_status IS NOT NULL)
                                                         OR needs_manual_check)) AS relevant_incomplete
            FROM market.panel_watchlist''')).mappings().one()
        items.append(_criterion('2c', 'The candidate panel list has been reviewed and marked by me',
                                'met' if panels['candidates'] and panels['marked'] == panels['candidates'] else 'not_met',
                                measure='panels marked / candidates', value=panels['marked'], target=panels['candidates']))
        items.append(_criterion('2c', 'Every marked panel has expiry and status filled in, or needs_manual_check set',
                                'met' if panels['relevant'] and panels['relevant_incomplete'] == 0 else 'not_met',
                                measure='relevant panels missing expiry/status without the manual-check flag',
                                value=panels['relevant_incomplete'], target=0, evidence=dict(panels)))
        items.append(_criterion('2c', 'Alerts are tested with a fake date', 'not_measurable_here',
                                note='Covered by the automated panel reminder tests (12/6/3 months); see CI.'))

        # ---------------------------------------------------------------- Phase 2d
        forward = conn.execute(text("SELECT count(*) FROM market.current_forward")).scalar()
        amended = conn.execute(text('''SELECT count(*) FROM market.current_analytical_contracts
            WHERE current_value IS DISTINCT FROM original_value''')).scalar()
        items.append(_criterion('2d', 'Procurement plans and contract amendments are loaded', 'met' if forward and amended else 'not_met',
                                measure='forward plan/notice rows; contracts whose current value reflects an amendment',
                                value=dict(forward_rows=forward, amended_contracts=amended)))
        sample = _rows(conn, '''SELECT cn_id, buyer, supplier, original_value, payload->>'procurement_method' AS method,
            payload->>'exemption' AS exemption, payload->>'is_indigenous_exemption' AS indigenous_exemption, source_url
            FROM market.current_analytical_contracts WHERE payload->>'category_tag' IS NOT NULL
              AND (payload->>'is_indigenous_exemption' IN ('1','true','True') OR original_value BETWEEN 80000 AND 200000)
              AND first_published>:past ORDER BY md5(cn_id) LIMIT 20''', past=past)
        items.append(_criterion('2d', 'IPP lever flags are checked by hand on a sample of 20 contracts', 'owner_review', rows=sample,
                                note='Open each source_url and confirm the method/exemption flags.'))
        from .levers import load_ipp_rules
        ipp = load_ipp_rules()
        items.append(_criterion('2d', 'All thresholds are in config with source URLs', 'met' if ipp.get('sources') else 'not_met',
                                evidence=dict(sources=ipp.get('sources'), retrieved=ipp.get('retrieved'))))

        # ---------------------------------------------------------------- Phase 2e
    from .research import build_rap
    rap = build_rap(engine, as_of)
    current = [r for r in rap['targets'] if r['period_status'] == 'within_published_period']
    share = _ratio(sum(bool(r['candidate_abns']) for r in rap['targets']), len(rap['targets']))
    items.append(_criterion('2e', 'At least 90% of RAP holders matched to an ABN', 'met' if share and share >= .9 else 'not_met',
                            measure='RAP directory entries with at least one candidate ABN', value=share, target=0.9,
                            evidence=dict(entries=len(rap['targets']), current_plans=len(current))))
    with engine.connect() as conn:
        approved = conn.execute(text('SELECT count(*) FROM market.current_approved_rap_extractions')).scalar()
        targets = _rows(conn, 'SELECT * FROM market.v_rap_targets LIMIT 25') if _exists(conn, 'market.v_rap_targets') else []
        score = _exists(conn, 'market.v_account_score')
        lanes = _rows(conn, '''SELECT lane, count(*) AS accounts FROM market.v_account_score GROUP BY lane''') if score else []
    items.append(_criterion('2e', 'Extraction is spot-checked on 30 RAPs with at least 90% field accuracy', 'not_met',
                            measure='approved page-cited extractions', value=approved, target=30,
                            note='Needs a model provider key and a budget for the PDF extraction, then a 30-plan spot check.'))
    items.append(_criterion('2e', 'v_rap_targets top 25 reviewed by me', 'owner_review' if targets else 'not_met', rows=targets,
                            note=None if targets else 'View not deployed yet'))
    items.append(_criterion('MVP', 'Every score shows its components (federal and private lanes ranked)',
                            'owner_review' if lanes else 'not_met', rows=lanes,
                            note=None if lanes else 'v_account_score not deployed yet'))
    items.append(_criterion('MVP', 'The Monday digest arrives with sensible top-20 lists for the federal and private lanes',
                            'not_met', note='Research goes into the daily 08:15 brief; there is no separate weekly digest.'))
    summary = {}
    for item in items:
        summary.setdefault(item['phase'], {}).setdefault(item['status'], 0)
        summary[item['phase']][item['status']] += 1
    return dict(as_of=as_of, status='measured; sign-off is the owner\'s decision', summary=summary, criteria=items)


def _cell(value):
    if value is None:
        return ''
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    return json.dumps(value, default=json_value)


def write_acceptance(report, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory/'acceptance.json').write_text(json.dumps(report, default=json_value, indent=2), encoding='utf-8')
    label = dict(met='Met', not_met='Not met', owner_review='Needs your review', not_measurable_here='Checked elsewhere')
    parts = ['<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
             '<title>Phase sign-off</title><style>body{font:15px/1.5 system-ui,sans-serif;margin:16px;max-width:1000px}'
             'table{border-collapse:collapse;width:100%}td,th{border:1px solid #ccc;padding:4px 6px;text-align:left;vertical-align:top}'
             '.met{color:#1a7f37}.not_met{color:#b42318}.owner_review{color:#9a6700}.scroll{overflow-x:auto}</style>',
             '<h1>Phase sign-off: build spec done criteria</h1><p>As of ' + html.escape(str(report['as_of'])) + '. '
             'Measured from the research database; sign-off stays your decision.</p>']
    for item in report['criteria']:
        parts.append('<h3>Phase ' + html.escape(item['phase']) + ': ' + html.escape(item['spec']) + '</h3><p class="'
                     + item['status'] + '"><b>' + label[item['status']] + '</b>'
                     + (' — ' + html.escape(str(item['measure'])) + ': ' + html.escape(json.dumps(item['value'], default=json_value))
                        + (' (target ' + html.escape(str(item['target'])) + ')' if item['target'] is not None else '')
                        if item['measure'] else '') + '</p>')
        if item['note']:
            parts.append('<p>' + html.escape(item['note']) + '</p>')
        if item['rows']:
            keys = list(item['rows'][0])
            parts.append('<div class="scroll"><table><tr>' + ''.join('<th>' + html.escape(k) + '</th>' for k in keys) + '</tr>'
                         + ''.join('<tr>' + ''.join('<td>' + html.escape(_cell(r.get(k))[:300]) + '</td>' for k in keys)
                                   + '</tr>' for r in item['rows']) + '</table></div>')
    (directory/'acceptance.html').write_text(''.join(parts), encoding='utf-8')
    return dict(summary=report['summary'], json=str(directory/'acceptance.json'), html=str(directory/'acceptance.html'))
