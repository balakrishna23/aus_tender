"""Contact-free account export and CRM reconciliation. No external CRM writes or outreach."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from .analytics import build_report
from .research import build_rap,build_state
from .targets import build_targets
from .reporting import write_report_files,json_value


def reconcile(rows,relationships,limit):
    if not 1<=limit<=1000:raise ValueError('Invalid per-lane export limit')
    seen=set();out=[];counts={};excluded=0
    for original in rows:
        row=dict(original);key=row['account_key']
        if key in seen:continue
        seen.add(key);relationship=relationships.get(key,{})
        status=relationship.get('relationship_status','unknown')
        if status=='do_not_pursue':excluded+=1;continue
        lane=row['lane']
        if counts.get(lane,0)>=limit:continue
        row.update(relationship_status=status,last_activity_date=relationship.get('last_activity_date'),
                   new_target=status not in ['active','past_client'],action='continue existing relationship' if status in ['active','past_client'] else 'review evidence and qualification')
        counts[lane]=counts.get(lane,0)+1;out.append(row)
    return out,excluded


def build_crm(engine,as_of,limit=20):
    federal=build_report(engine,as_of);rap=build_rap(engine,as_of);state=build_state(engine,as_of);targets=build_targets(engine,as_of)
    with engine.connect() as conn:
        relationships={r['payload']['buyer_key']:r['payload'] for r in conn.execute(text("SELECT payload FROM market.current_research WHERE source='relationships'")).mappings()}
    rows=[]
    for r in federal['buyers']:
        rows.append(dict(account_key=r['buyer_key'],organisation=r['buyer'],lane='federal',priority_rank=r['rank'],score=r['priority_score'],
            score_basis='relative federal research score; eligibility separate',why_now=f"{r['recent_awards_count']} recent recorded awards; {r['expiry_count_365d']} recorded end dates within a year",
            target_roles='ICT services / labour hire; specific role fit unverified',source_url=(r.get('evidence_contracts') or [{}])[0].get('source_url'),
            identity_status=r.get('identity_status','proposal; review required')))
    for r in rap['targets']:
        if r['period_status']!='within_published_period':continue
        rows.append(dict(account_key='rap:'+r['record_key'],organisation=r['organisation'],lane='private',priority_rank=r['rank'],score=r['score'],
            score_basis='RAP research points; not comparable to federal percentiles',why_now=r['why_now'],target_roles=r['target_roles'],
            source_url=r['document_url'],identity_status=r['entity_match_status']))
    # Historical/state and purpose-based healthcare lists are not presented as qualified scored prospects.
    state_accounts={}
    for r in state['contracts']:
        if not r['agency']:continue
        key='state:'+hashlib.sha256((r['state']+'|'+r['agency']).encode()).hexdigest()
        state_accounts.setdefault(key,dict(account_key=key,organisation=r['agency'],lane='state',priority_rank=None,score=None,
            score_basis='historical source directory; not scored',why_now=r['why_now'],target_roles=r['target_roles'],source_url=r['source_url'],identity_status='source label; not matched'))
    rows.extend(sorted(state_accounts.values(),key=lambda r:r['organisation']))
    for r in sorted(targets['healthcare_accounts'],key=lambda r:((r['organisation'] or ''),r['source'],r['record_id'])):
        rows.append(dict(account_key='health:'+r['source']+':'+r['record_id'],organisation=r['organisation'],lane='healthcare',priority_rank=None,score=None,
            score_basis='published health purpose/industry; not scored',why_now=r['why_now'],target_roles=r['target_roles'],source_url=r['source_url'],
            identity_status='source record; workforce need unknown'))
    output,excluded=reconcile(rows,relationships,limit)
    return dict(as_of=as_of,status='CRM-ready research export; no contact details or external writes',accounts=output,excluded_do_not_pursue=excluded,
        limitations=['Stable keys retain source identity; unconfirmed RAP/state/healthcare entities are not silently merged by name.',
                     'Federal and private research scores have different bases and are not combined into a calibrated win probability.',
                     'State history and healthcare-purpose records are unscored; neither establishes current budget, demand or readiness.',
                     'Existing clients/active relationships are marked; do-not-pursue records are excluded.',
                     'Import CRM relationship/activity, invitation and outcome CSVs with import-input; no CRM login, contact enrichment or outreach is automated.'])


def export_crm(report,directory):
    directory=Path(directory)
    result=write_report_files(report,directory,'crm_accounts',report['accounts'],
        ['account_key','organisation','lane','priority_rank','score','score_basis','relationship_status','new_target','action','why_now','target_roles','source_url','identity_status'],
        'CRM research export')
    manifest=dict(schema='illoura.crm_export.v1',as_of=report['as_of'],exported_at=datetime.now(timezone.utc),rows=len(report['accounts']),
                  csv_sha256=hashlib.sha256((directory/'crm_accounts.csv').read_bytes()).hexdigest(),contains_contact_data=False,external_writes=False)
    (directory/'crm_export_manifest.json').write_text(json.dumps(manifest,default=json_value,indent=2),encoding='utf-8')
    return result
