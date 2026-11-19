"""Reviewable federal buyer research priorities, never a claim of available work."""
from datetime import date, datetime, timedelta
import hashlib
import html
import json
import math
from pathlib import Path

from sqlalchemy import text

FAMILIES=['ICT services','labour hire and recruitment']


def rank(rows,config):
    weights=config['weights']
    if config.get('method')!='relative_percentiles' or set(weights)!={'awarded_value_365d','recent_awards_count','expiry_count_365d'}:
        raise ValueError('Unsupported scoring inputs/method')
    if any(not isinstance(v,(int,float)) or not math.isfinite(v) or v<0 for v in weights.values()) or abs(sum(weights.values())-1)>1e-9:
        raise ValueError('Scoring weights must be nonnegative and sum to one')
    result=[]
    for row in rows:
        output=dict(row);components={};coverage=0
        for field,weight in weights.items():
            value=row.get(field)
            known=[r[field] for r in rows if r.get(field) is not None]
            if value is None:
                components[field]=None;continue
            coverage+=weight
            percentile=0 if value<=0 else 100*(sum(v<value for v in known)+0.5*sum(v==value for v in known))/len(known)
            components[field]=round(percentile,4)
        output['priority_score']=round(sum(weights[k]*(v or 0) for k,v in components.items()),2)
        output['score_components']=components
        output['score_coverage_pct']=round(100*coverage,2)
        result.append(output)
    result.sort(key=lambda r:(-r['priority_score'],r['buyer'].casefold(),r['buyer_key']))
    for index,row in enumerate(result,1):row['rank']=index
    return result


def build_report(engine,as_of,config_path=None):
    if not isinstance(as_of,date):raise ValueError('Explicit as-of date required')
    config_path=Path(config_path) if config_path else Path(__file__).with_name('scoring.json')
    config_bytes=config_path.read_bytes();config=json.loads(config_bytes)
    source_manifest=Path(__file__).parents[1]/'source-manifest.json'
    code_bundle=json.loads(source_manifest.read_text()).get('code_bundle_sha256') if source_manifest.exists() else None
    params={'families':FAMILIES,'asof':as_of,'past':as_of-timedelta(days=365),'future':as_of+timedelta(days=365)}
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        procurement=conn.execute(text('''SELECT p.* FROM market.procurement_snapshots p
            JOIN market.current_procurement_snapshot c ON p.id=c.snapshot_id''')).mappings().first()
        if not procurement:raise ValueError('Import procurement facts before building research priorities')
        registers=[dict(r) for r in conn.execute(text('''SELECT s.id,s.source,s.effective_date,s.effective_date_basis,s.parser_revision,
            s.source_url,s.licence FROM market.current_snapshots c JOIN market.snapshots s ON s.id=c.snapshot_id''')).mappings()]
        query='''WITH relevant AS (SELECT *,first_published>:past AND first_published<=:asof AS recent,
            current_end_date>=:asof AND current_end_date<:future AS ending FROM market.current_analytical_contracts
            WHERE category_family=ANY(CAST(:families AS text[])))
            SELECT buyer_key,buyer,buyer_abn,count(*) AS stored_relevant_contracts,
            count(*) FILTER(WHERE recent) AS recent_awards_count,
            sum(original_value) FILTER(WHERE recent) AS awarded_value_365d,
            count(original_value) FILTER(WHERE recent) AS recent_awards_with_value,
            count(*) FILTER(WHERE ending) AS expiry_count_365d,
            count(*) FILTER(WHERE recent AND ending) AS awards_also_ending_count,
            sum(current_value) FILTER(WHERE ending) AS full_contract_value_ending_365d,
            count(*) FILTER(WHERE current_end_date IS NULL) AS missing_end_dates,
            count(*) FILTER(WHERE first_published IS NULL) AS missing_publication_dates,
            count(DISTINCT supplier_abn) FILTER(WHERE recent) AS recent_supplier_abns
            FROM relevant GROUP BY buyer_key,buyer,buyer_abn
            HAVING count(*) FILTER(WHERE recent)>0 OR count(*) FILTER(WHERE ending)>0'''
        rows=[dict(r) for r in conn.execute(text(query),params).mappings()]
        matches={r['buyer_key']:dict(r) for r in conn.execute(text('SELECT * FROM market.buyer_resolution')).mappings()}
        panel_rows=[dict(r) for r in conn.execute(text('''SELECT c.buyer_key,l.son_id,count(DISTINCT c.cn_id) AS linked_contracts,
            max(p.title) AS panel_title,max(p.current_expiry) AS notice_expiry,
            max(p.source_url) AS panel_source_url,
            count(DISTINCT p.current_expiry) AS distinct_expiry_claims
            FROM market.current_analytical_contracts c JOIN market.procurement_panel_links l ON l.snapshot_id=c.snapshot_id AND l.cn_id=c.cn_id
            LEFT JOIN market.procurement_panels p ON p.snapshot_id=l.snapshot_id AND p.son_id=l.son_id
            WHERE c.category_family=ANY(CAST(:families AS text[])) AND ((c.first_published>:past AND c.first_published<=:asof)
              OR (c.current_end_date>=:asof AND c.current_end_date<:future))
            GROUP BY c.buyer_key,l.son_id'''),params).mappings()]
        supplier_rows=[dict(r) for r in conn.execute(text('''SELECT buyer_key,supplier,supplier_abn,
            count(*) FILTER(WHERE first_published>:past AND first_published<=:asof) AS awards,
            sum(original_value) FILTER(WHERE first_published>:past AND first_published<=:asof) AS original_awarded_value,
            count(*) FILTER(WHERE current_end_date>=:asof AND current_end_date<:future) AS ending_contracts,
            sum(current_value) FILTER(WHERE current_end_date>=:asof AND current_end_date<:future) AS full_value_ending
            FROM market.current_analytical_contracts
            WHERE category_family=ANY(CAST(:families AS text[])) AND ((first_published>:past AND first_published<=:asof)
                OR (current_end_date>=:asof AND current_end_date<:future))
            GROUP BY buyer_key,supplier,supplier_abn ORDER BY original_awarded_value DESC NULLS LAST'''),params).mappings()]
        evidence_rows=[dict(r) for r in conn.execute(text('''SELECT buyer_key,cn_id,source_url,first_published,current_end_date,
            original_value,current_value,first_published>:past AND first_published<=:asof AS recent,
            current_end_date>=:asof AND current_end_date<:future AS ending
            FROM market.current_analytical_contracts WHERE category_family=ANY(CAST(:families AS text[]))
            AND ((first_published>:past AND first_published<=:asof) OR (current_end_date>=:asof AND current_end_date<:future))
            ORDER BY GREATEST(original_value,current_value) DESC NULLS LAST'''),params).mappings()]
        excluded_legacy=conn.execute(text('''SELECT count(*) FROM market.current_contracts
            WHERE issues @> '["legacy_amendment_identity_requires_review"]'::jsonb''')).scalar()
    panels={};suppliers={};ending_suppliers={};evidence={};supplier_counts={};evidence_counts={};all_evidence={}
    for item in panel_rows:
        item['expiry_status']='conflicting_source_dates; needs_manual_check' if item['distinct_expiry_claims']>1 else 'notice_only; intake_and_extensions_unknown'
        if item['distinct_expiry_claims']>1:item['notice_expiry']=None
        panels.setdefault(item['buyer_key'],[]).append(item)
    for item in supplier_rows:
        if item['awards']:
            supplier_counts[item['buyer_key']]=supplier_counts.get(item['buyer_key'],0)+1
            target=suppliers.setdefault(item['buyer_key'],[])
            if len(target)<5:target.append(item)
        if item['ending_contracts']:ending_suppliers.setdefault(item['buyer_key'],[]).append(item)
    for item in evidence_rows:
        evidence_counts[item['buyer_key']]=evidence_counts.get(item['buyer_key'],0)+1
        all_evidence.setdefault(item['buyer_key'],[]).append(item)
        target=evidence.setdefault(item['buyer_key'],[])
        if len(target)<5:target.append(item)
    for row in rows:
        if row['recent_awards_count']==0:row['awarded_value_365d']=0
        match=matches.get(row['buyer_key'])
        row.update(lane='federal',match_status=match['match_status'] if match else 'unmatched',
                   review_status=match['review_status'] if match else 'unreviewed',
                   register_context_stale=match['register_context_stale'] if match else True,
                   proposed_organisation_id=match['organisation_id'] if match else None,
                   confirmed_organisation_id=match['confirmed_organisation_id'] if match else None,
                   access_status='unknown; Illoura panel profile not verified',eligibility_status='not_assessed',
                   invitation_history='unknown; public awards do not establish invitations',bench_readiness='unknown',
                   top_suppliers_recent_awards=suppliers.get(row['buyer_key'],[]),
                   top_suppliers_ending_contracts=sorted(ending_suppliers.get(row['buyer_key'],[]),key=lambda r:-(r['full_value_ending'] or 0))[:5],
                   known_panel_routes=panels.get(row['buyer_key'],[]),evidence_contracts=evidence.get(row['buyer_key'],[]),
                   evidence_contracts_total_count=evidence_counts.get(row['buyer_key'],0),evidence_sample_limit=5,
                   recent_supplier_rows_total_count=supplier_counts.get(row['buyer_key'],0),
                   ending_supplier_rows_total_count=len(ending_suppliers.get(row['buyer_key'],[])),
                   recent_award_evidence=sorted([e for e in all_evidence.get(row['buyer_key'],[]) if e['recent']],key=lambda e:-(e['original_value'] or 0))[:5],
                   ending_contract_evidence=sorted([e for e in all_evidence.get(row['buyer_key'],[]) if e['ending']],key=lambda e:-(e['current_value'] or 0))[:5],
                   target_roles=['ICT/workforce lead','Procurement lead'],
                   why_now=f"{row['recent_awards_count']} relevant contracts first published in the trailing 365 days; {row['expiry_count_365d']} recorded end dates in the next 365 days ({row['awards_also_ending_count']} contracts appear in both groups). End dates are research signals, not announced re-tenders.")
        row['warnings']=[]
        if row['register_context_stale']:row['warnings'].append('Buyer identity proposals need rebuilding against current register snapshots')
        if row['review_status']=='needs_recheck':row['warnings'].append('Previously reviewed identity has changed; human recheck required')
        if row['review_status']=='rejected':row['warnings'].append('Human reviewer rejected the identity mapping')
        row['financial_value_coverage_pct']=round(100*row['recent_awards_with_value']/row['recent_awards_count'],2) if row['recent_awards_count'] else None
    warnings=[]
    checkpoint=procurement['metadata'].get('sources',{}).get('contract_api',{}).get('modified_through')
    try:
        source_date=datetime.fromisoformat(checkpoint).date() if checkpoint else None
    except (TypeError,ValueError):source_date=None
    age=(as_of-source_date).days if source_date else None
    if age is None:warnings.append('Contract collection freshness is unverified; export time is not a collection checkpoint')
    elif age>config.get('max_source_age_days',7):warnings.append('Contract collection checkpoint is stale; treat priorities as historical research')
    if procurement['exported_at'].date()>as_of:warnings.append('Snapshot was exported after the requested as-of date; this is not a historical point-in-time reconstruction')
    if excluded_legacy:warnings.append(str(excluded_legacy)+' legacy amendment-identity rows retained for review and excluded from analytical totals')
    return {'as_of':as_of,'status':'draft_for_review','scope':'Federal ICT services and labour hire/recruitment; source buyer labels remain separate until identity review',
            'code_bundle_sha256':code_bundle,
            'warnings':warnings,'contract_collection_checkpoint':checkpoint,'contract_checkpoint_age_days':age,
            'excluded_legacy_amendment_rows':excluded_legacy,
            'procurement_snapshot_id':str(procurement['id']),'procurement_exported_at':procurement['exported_at'],
            'procurement_parser_revision':procurement['parser_revision'],
            'procurement_source_sha256':procurement['sha256'],'register_snapshots':registers,
            'procurement_source_metadata':procurement['metadata'],
            'coverage':'Stored collector records; completeness of all official history is not asserted.',
            'scoring_config':config,'scoring_config_sha256':hashlib.sha256(config_bytes).hexdigest(),
            'definitions':{'awarded_value_365d':'Sum of original published contract values for notices first published in the trailing 365 days; not payments or annual expenditure.',
                           'full_contract_value_ending_365d':'Full current reported value of contracts with recorded end dates in the next 365 days; not remaining value or forecast revenue.',
                           'priority_score':'Relative research priority within this snapshot; not eligibility, likelihood of winning or delivery readiness.',
                           'panels':'Known links from collected contract pages; absence is unknown, not proof of no panel use.'},
            'buyers':rank(rows,config)}


def write_report(report,directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(report,default=str,indent=2)
    (directory/'federal_buyers.json').write_text(raw,encoding='utf-8')
    cards=[]
    for row in report['buyers'][:25]:
        money=lambda value:'Unknown' if value is None else '${:,.0f}'.format(value)
        links=' '.join('<a href="'+html.escape(e['source_url'] or '',quote=True)+'">'+html.escape(e['cn_id'])+'</a>' for e in row['evidence_contracts'] if str(e['source_url'] or '').startswith('https://'))
        suppliers=row['top_suppliers_recent_awards'] or row['top_suppliers_ending_contracts']
        supplier_text=', '.join(html.escape(s['supplier'] or 'Supplier name unavailable') for s in suppliers[:3]) or 'No supplier names in the selected evidence window'
        panel_text='; '.join(html.escape(p['son_id']+' — '+(p['panel_title'] or 'Notice title unavailable')) for p in row['known_panel_routes'][:5]) or 'No panel link in collected evidence; purchasing route remains unknown'
        components='; '.join(html.escape(k.replace('_',' '))+': '+('unknown' if v is None else str(round(v,1))) for k,v in row['score_components'].items())
        cards.append('<article><h2>'+str(row['rank'])+'. '+html.escape(row['buyer'])+'</h2><p>'+html.escape(row['why_now'])+'</p><dl>'
                     '<dt>Research priority</dt><dd>'+str(row['priority_score'])+'/100</dd><dt>Original awarded value, trailing 365 days</dt><dd>'+money(row['awarded_value_365d'])+'</dd>'
                     '<dt>Identity proposal / review</dt><dd>'+html.escape(row['match_status']+' / '+row['review_status'])+'</dd><dt>Access and eligibility</dt><dd>Not verified</dd>'
                     '<dt>Supplier sample from the selected evidence window</dt><dd>'+supplier_text+'</dd><dt>Known panel routes (up to five shown)</dt><dd>'+panel_text+'</dd></dl><p>'+html.escape(' '.join(row['warnings']))+'</p>'
                     '<details><summary>Score components and evidence coverage</summary><p>'+components+'</p><p>Available score inputs: '+str(row['score_coverage_pct'])+'%. Percentiles are relative to this report; eligibility and delivery readiness are separate.</p></details>'
                     '<p>Evidence sample '+str(len(row['evidence_contracts']))+' of '+str(row['evidence_contracts_total_count'])+' contracts: '+links+'</p></article>')
    page='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Federal buyer research priorities</title>
<style>body{font:16px/1.5 system-ui,sans-serif;max-width:1050px;margin:32px auto;padding:0 20px;background:#f4f6f8;color:#163144}article{background:white;border:1px solid #d9e0e5;border-radius:10px;padding:20px;margin:18px 0}h1,h2{line-height:1.2}dt{font-weight:600}dd{margin:0 0 12px}a{margin-right:12px;color:#075c86}</style>
<h1>Federal buyer research priorities</h1><p>Draft for internal review. As of '''+str(report['as_of'])+'''. Top 25 source buyer labels.</p><p>Contract values are not annual expenditure. Recorded expiry dates do not establish a re-tender. Panel access, eligibility, invitations and candidate availability remain unverified.</p><p>'''+html.escape(' '.join(report['warnings']))+'</p>'+''.join(cards)+'<p>Full evidence, score components, source dates and limitations are in federal_buyers.json.</p></html>'
    (directory/'federal_buyers.html').write_text(page,encoding='utf-8')
    return {'buyers':len(report['buyers']),'json':str(directory/'federal_buyers.json'),'html':str(directory/'federal_buyers.html')}
