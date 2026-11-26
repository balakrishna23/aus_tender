"""Build the spec views in one repeatable-read pass and publish atomically.

Builders receive an engine-shaped facade over the same connection. Their local
connection/transaction contexts cannot commit, close, or change the shared snapshot.
"""
from collections import Counter, defaultdict
from contextlib import nullcontext
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import uuid

from sqlalchemy import text

from . import analytics, buyict, levers, targets, research, panel_access, forward, changes, inputs
from .account_score import CONFIG, load_config, validate_config, score_accounts
from .parsers import name_key

VIEW_NAMES = ('account_score','buyict_buyers','buyict_gaps','ipp_gap','ipp_levers','prime_targets',
              'partner_candidates','indigenous_partners','rap_targets','panel_access','forward_pipeline',
              'demand_vs_bench','lookalikes','whats_changed')
ROW_LIMIT = 10000


class SnapshotConnection:
    def __init__(self, connection):self.connection=connection
    def execution_options(self, **kwargs):return self
    def begin(self):return nullcontext(self)
    def execute(self, *args, **kwargs):return self.connection.execute(*args, **kwargs)
    def __enter__(self):return self
    def __exit__(self, *args):return False


class SnapshotEngine:
    def __init__(self, connection):self.connection=SnapshotConnection(connection)
    def connect(self):return self.connection


def json_default(value):
    if isinstance(value,Decimal):return float(value)
    if isinstance(value,(date,uuid.UUID)):return str(value)
    raise TypeError('Unsupported derived value')


# Explicitly omit human reviewers, internal notes and raw source payloads from
# generated analytical records. Builders already enforce register-person exclusions.
PRIVATE_FIELDS = {'verified_by','reviewer','reviewed_by','notes','payload','last_activity_date'}


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in PRIVATE_FIELDS}
    if isinstance(value,(list,tuple)):return [clean(v) for v in value]
    if isinstance(value,Decimal):return float(value)
    return value


def shape_row(row, lane='federal', position=1):
    row=clean(dict(row))
    account=next((row.get(k) for k in ('account','organisation','buyer','owner_agency','panel_name','portfolio','role_family') if row.get(k)),None)
    key=next((row.get(k) for k in ('account_key','buyer_key','organisation_id','abn','supplier_abn','record_key','record_id','son_id') if row.get(k)),None)
    roles=row.get('target_roles') or ['Procurement lead','Workforce lead']
    if isinstance(roles,str):roles=[roles]
    row.update(lane=row.get('lane') or lane,account=account,organisation=account,
               account_key=str(key) if key is not None else 'derived:'+hashlib.sha256(json.dumps(row,sort_keys=True,default=json_default).encode()).hexdigest(),
               abn=row.get('abn') or row.get('supplier_abn'),rank=position,target_roles=roles,
               why_now=row.get('why_now') or 'Review retained evidence and qualification; buying intent unverified',
               source_url=row.get('source_url') or row.get('document_url') or row.get('evidence_url') or row.get('publication_page'))
    row.setdefault('evidence',[])
    return row


def invitation_matches(buyers, records):
    """Only exact stable keys or unambiguous normalised full labels; never fuzzy."""
    names=Counter(name_key(b['buyer']) for b in buyers)
    result={}
    for buyer in buyers:
        matches=[]
        for record in records:
            key=record.get('buyer_key','')
            if key==buyer['buyer_key']:
                matches.append((record,'exact_buyer_key'))
            elif names[name_key(buyer['buyer'])]==1 and name_key(key)==name_key(buyer['buyer']):
                matches.append((record,'unique_normalised_full_buyer_label'))
        invited=[basis for r,basis in matches if r.get('invitation_status')=='invited']
        result[buyer['buyer_key']]=dict(recorded_invitation=bool(invited),
            invitation_basis=('Recorded invited: '+', '.join(sorted(set(invited)))) if invited else
                'No recorded invited status; '+('matched input records: '+', '.join(sorted({basis for _,basis in matches})) if matches else 'no exact key or unique full-label input match')+
                '; absence does not establish never invited',matched_invitation_records=len(matches))
    return result


def lookalikes(accounts, client_keys):
    """Four equally weighted dimensions. Unknown dimensions contribute no similarity."""
    clients=[r for r in accounts if r['account_key'] in client_keys]
    out=[]
    for account in accounts:
        best=None
        for client in clients:
            if client['account_key']==account['account_key']:continue
            dims={}
            for field in ('type','size','portfolio'):
                a=account.get('features',{}).get(field);b=client.get('features',{}).get(field)
                dims[field]=None if a is None or b is None else float(str(a).casefold()==str(b).casefold())
            a=account.get('features',{}).get('category_spend');b=client.get('features',{}).get('category_spend')
            if a and b:
                keys=set(a)|set(b);den=sum(max(a.get(k,0),b.get(k,0)) for k in keys)
                dims['category_spend']=sum(min(a.get(k,0),b.get(k,0)) for k in keys)/den if den else None
            else:dims['category_spend']=None
            coverage=25*sum(v is not None for v in dims.values())
            similarity=25*sum(v for v in dims.values() if v is not None) if coverage else None
            candidate=dict(similarity=similarity,similarity_coverage_pct=coverage,dimensions=dims,matched_client_key=client['account_key'])
            if best is None or (similarity is not None,similarity or 0,coverage)>(best['similarity'] is not None,best['similarity'] or 0,best['similarity_coverage_pct']):best=candidate
        if best is None:best=dict(similarity=None,similarity_coverage_pct=0,dimensions={},matched_client_key=None)
        out.append(dict(account_key=account['account_key'],account=account['account'],lane=account['lane'],**best,
                        why_now='Similarity to recorded past clients; missing dimensions retain their weight; identity proposals are not confirmed',
                        target_roles=account['target_roles'],source_url=account.get('source_url')))
    return out


def build_view_rows(conn, as_of, config=None):
    config=config if config is not None else load_config()
    snapshot=SnapshotEngine(conn)
    federal=analytics.build_report(snapshot,as_of)
    ict=buyict.build_buyict(snapshot,as_of)
    ipp=levers.build_levers(snapshot,as_of)
    target=targets.build_targets(snapshot,as_of)
    rap=research.build_rap(snapshot,as_of)
    # build_state requires a source; missing source is deliberately empty, not failed.
    has_state=conn.execute(text("SELECT EXISTS(SELECT 1 FROM market.current_research WHERE source='state_contracts')")).scalar()
    state=research.build_state(snapshot,as_of) if has_state else dict(contracts=[],limitations=['No retained WA state-contract input'])
    panels=panel_access.build_panel_access(snapshot,as_of)
    pipeline=forward.build_forward(snapshot,as_of)
    bench=inputs.build_bench(snapshot,as_of)
    delta=changes.build_changes(snapshot,as_of-timedelta(days=7),as_of)
    business=[dict(r) for r in conn.execute(text("SELECT source,payload FROM market.current_research WHERE source IN ('relationships','outcomes','invitations')")).mappings()]
    relations={r['payload']['buyer_key']:r['payload'] for r in business if r['source']=='relationships'}
    client_keys={k for k,p in relations.items() if p.get('relationship_status')=='past_client'}
    client_keys.update(r['payload']['buyer_key'] for r in business if r['source']=='outcomes' and r['payload'].get('outcome')=='won')
    invitation=invitation_matches(ict['buyers'],[r['payload'] for r in business if r['source']=='invitations'])
    profile={r.key:r.value for r in conn.execute(text('SELECT key,value FROM market.illoura_profile'))}
    # Only the accounts' own ABNs (plus the complete ACNC/ORIC registers): the national ABN/ASIC registers
    # hold millions of rows (migration 024) and must never be read whole.
    wanted=sorted({a for a in [*(b.get('buyer_abn') for b in federal['buyers']),
                               *(s.get('supplier_abn') for s in target['supplier_profiles']),
                               *(p.get('supplier_abn') for p in ipp['mmr_primes']),
                               *(h.get('abn') for h in target['healthcare_accounts'])] if a})
    registry=[dict(r) for r in conn.execute(text('''SELECT e.abn,e.name,e.source,e.attributes,e.entity_type,s.source_url
        FROM market.current_register_entities e JOIN market.register_snapshots s ON s.id=e.snapshot_id
        WHERE NOT e.is_individual AND (e.source IN ('acnc','oric') OR e.abn=ANY(:abns))'''),dict(abns=wanted)).mappings()]
    registered={};safe_names={}
    registry.sort(key=lambda r:(r['source']!='abn',r['source']!='asic_companies',r['name']))
    for r in registry:
        if r['abn']:safe_names.setdefault(r['abn'],r['name'])
        if r['abn']:registered.setdefault(r['abn'],{}).update({k:v for k,v in dict(type=r['entity_type'],size=r['attributes'].get('size'),source_url=r['source_url']).items() if v is not None})
    buyer_context={r['buyer_key']:dict(r) for r in conn.execute(text('''SELECT r.buyer_key,o.portfolio,o.payload->>'Type of Body' AS body_type,
        r.review_status,r.register_context_stale FROM market.buyer_resolution r LEFT JOIN market.organisation_observations o
        ON o.organisation_id=r.confirmed_organisation_id AND o.source='agor' ''')).mappings()}
    expiry={r.buyer_key:r.n for r in conn.execute(text('''SELECT buyer_key,count(*) AS n FROM market.current_analytical_contracts
        WHERE current_end_date>=:lo AND current_end_date<=:hi AND category_family=ANY(:families) GROUP BY buyer_key'''),
        dict(lo=panel_access.months_before(as_of,-6),hi=panel_access.months_before(as_of,-12),families=analytics.FAMILIES))}
    category_spend=defaultdict(dict)
    for r in conn.execute(text('''SELECT buyer_key,payload->>'category_tag' AS category,sum(original_value) AS value
        FROM market.current_analytical_contracts WHERE first_published>:past AND first_published<=:asof
        AND category_family=ANY(:families) GROUP BY 1,2'''),dict(past=as_of-timedelta(days=365),asof=as_of,families=analytics.FAMILIES)).mappings():
        if r['value'] is not None and r['category']:category_spend[r['buyer_key']][r['category']]=max(0,float(r['value']))
    supplier_category_spend=defaultdict(dict)
    for r in conn.execute(text('''SELECT supplier_abn,payload->>'category_tag' AS category,sum(original_value) AS value
        FROM market.current_analytical_contracts WHERE first_published>:past AND first_published<=:asof
        AND supplier_abn IS NOT NULL AND payload->>'category_tag'=ANY(:tags) GROUP BY 1,2'''),
        dict(past=as_of-timedelta(days=1095),asof=as_of,tags=forward.TAGS)).mappings():
        if r['value'] is not None:supplier_category_spend[r['supplier_abn']][r['category']]=max(0,float(r['value']))
    panel_by_id={p['son_id']:p for p in panels['panels']}
    linked_spend=defaultdict(list)
    for r in conn.execute(text('''SELECT DISTINCT c.buyer_key,c.cn_id,l.son_id,c.original_value AS value
        FROM market.current_analytical_contracts c JOIN market.procurement_panel_links l USING(snapshot_id,cn_id)
        WHERE c.first_published>:past AND c.first_published<=:asof AND c.category_family=ANY(:families)'''),dict(past=as_of-timedelta(days=365),asof=as_of,families=analytics.FAMILIES)).mappings():linked_spend[r['buyer_key']].append(dict(r))
    views={name:[] for name in VIEW_NAMES};accounts={}
    def add(row,lane,key=None):
        row=shape_row(dict(row,account_key=key or row.get('account_key')),lane)
        row.setdefault('inputs',{});row.setdefault('basis',{});row.setdefault('features',{})
        row.setdefault('penalty_inputs',dict(blocker=None,insourcing=None))
        row.setdefault('blockers',dict(status='unknown',gaps=[],unknown=['Eligibility requirements and/or profile not verified']))
        row['penalty_basis']=dict(blocker='Verified eligibility requirements compared with Illoura profile; incomplete requirements/profile remain unknown',insourcing='No insourcing input source')
        relationship=relations.get(row['account_key'],{})
        row.update(relationship_status=relationship.get('relationship_status','unknown'),already_engaged=row['account_key'] in client_keys or relationship.get('relationship_status') in ('active','researching','past_client'))
        if row.get('abn'):
            context=registered.get(row['abn'],{});row['features'].update(context)
            row['source_url']=row.get('source_url') or context.get('source_url')
        accounts[(lane,row['account_key'])]=row
        return row
    rap_names=defaultdict(list)
    for r in rap['targets']:
        if r['period_status']=='within_published_period':rap_names[name_key(r['organisation'])].append(r)
    def current_rap(name):
        matches=rap_names.get(name_key(name or ''),[])
        return matches[0] if len(matches)==1 else None
    ict_by_key={b['buyer_key']:b for b in ict['buyers']}
    fed_by_key={b['buyer_key']:b for b in federal['buyers']}
    ipp_by_port={levers._portfolio_key(p['portfolio']):p for p in ipp['portfolio_results']}
    lever_counts=Counter()
    for lever,rows in [('exemption_16',ipp['exemption_16_by_buyer']),('set_aside_band',ipp['set_aside_band_by_buyer'])]:
        for r in rows:
            lever_counts[r['buyer_key']]+=r['contracts']
            views['ipp_levers'].append(dict(r,lever=lever,source_url=ipp.get('sources',{}).get('ipp_guidelines'),why_now='Published IPP lever use; set-aside eligibility not established'))
    for b in ict['buyers']:
        row=dict(b,**invitation[b['buyer_key']],why_now='Relevant BuyICT purchasing; review RFQ invitation coverage',target_roles=['ICT procurement lead','Panel sourcing lead'])
        views['buyict_buyers'].append(row)
        if b['relevant_contracts_36m'] and not row['recorded_invitation']:views['buyict_gaps'].append(row)
    for key in sorted(set(fed_by_key)|set(ict_by_key)):
        b=fed_by_key.get(key);i=ict_by_key.get(key);base=b or i
        row=add(dict(base,source_url=(base.get('evidence_contracts') or [{}])[0].get('source_url')),'federal',key)
        row['abn']=base.get('buyer_abn');row['organisation_id']=base.get('confirmed_organisation_id');row['features']['category_spend']=category_spend.get(key)
        ctx=buyer_context.get(key,{})
        reviewed=ctx.get('review_status')=='approved' and not ctx.get('register_context_stale')
        if reviewed:row['features'].update(portfolio=ctx.get('portfolio'),type=ctx.get('body_type'))
        portfolio=ipp_by_port.get(levers._portfolio_key(ctx.get('portfolio'))) if reviewed else None
        gap=max(0,1-min(v for v in (portfolio['value_ratio'],portfolio['volume_ratio']) if v is not None)) if portfolio and any(portfolio[k] is not None for k in ('value_ratio','volume_ratio')) else None
        if portfolio:views['ipp_gap'].append(dict(portfolio,source_url=ipp.get('sources',{}).get('ipp_page'),buyer_key=key,buyer=base['buyer'],gap=gap,why_now='Reviewed buyer portfolio: historical IPP performance; agency shortfall not inferred'))
        signals=[s for s in pipeline['signals'] if s.get('buyer_key')==key or name_key(s.get('buyer') or '')==name_key(base['buyer'])]
        linked=linked_spend.get(key,[])
        membership_known=bool(linked) and all(panel_by_id.get(p['son_id'],{}).get('illoura_status') in ('member','applied','not_applied','not_eligible') for p in linked)
        access=sum(float(p['value']) for p in {p['cn_id']:p for p in linked if panel_by_id[p['son_id']]['illoura_status']!='member'}.values()) if membership_known and all(p['value'] is not None for p in linked) else None
        r=current_rap(base['buyer'])
        row['inputs'].update(spend=float(b['awarded_value_365d']) if b and b['awarded_value_365d'] is not None else None,
            frequency=b['recent_awards_count'] if b else None,expiry=expiry.get(key,0) if b else None,
            buyict_gap=float(i['relevant_value_36m']) if i and not invitation[key]['recorded_invitation'] else 0 if i else None,
            ipp_gap=gap,levers=lever_counts.get(key,0) if b else None,
            forward=sum(s['signal']=='published_plan' and s.get('date') is not None for s in signals) if pipeline['snapshots'] else None,
            open_opportunities=sum(s['signal']=='open_notice_requires_live_recheck' for s in signals) if pipeline['snapshots'] else None,rap=1 if r else None,panel_access=access)
        if not base.get('why_now'):row['why_now']=str(i['relevant_contracts_36m'])+' relevant BuyICT contracts over 36 months; review RFQ invitation coverage' if i else 'Relevant federal award evidence'
        row['basis']['buyict_gap']=invitation[key]['invitation_basis'] if i else 'No linked BuyICT input for this buyer'
        row['evidence']=base.get('evidence_contracts',[])
        eligibility_rows=[panel_by_id[p['son_id']]['eligibility'] for p in linked if p['son_id'] in panel_by_id]
        if eligibility_rows:
            gaps=sorted({g for e in eligibility_rows for g in e['gaps']});unknown=sorted({g for e in eligibility_rows for g in e['unknown']})
            row['blockers']=dict(status='blocked' if gaps else 'unknown' if unknown else 'requirements_met',gaps=gaps,unknown=unknown)
            row['penalty_inputs']['blocker']=1 if gaps else None if unknown or len(eligibility_rows)!=len(linked) else 0
    supplier_by_abn={s['supplier_abn']:s for s in target['supplier_profiles'] if s.get('organisation')}
    # Never use contract supplier labels: they can be individuals. Only registry-safe names.
    primes=[]
    for p in ipp['mmr_primes']:
        safe=supplier_by_abn.get(p['supplier_abn'])
        if not safe and p['supplier_abn'] in safe_names:safe=dict(organisation=safe_names[p['supplier_abn']])
        if safe:primes.append(dict(p,lane='private',supplier=safe['organisation'],organisation=safe['organisation'],source_url=ipp.get('sources',{}).get('ipp_guidelines'),why_now=str(p['active_high_value_contracts'])+' active high-value MMR-segment contracts; research subcontracting fit'))
    prime_by_abn={p['supplier_abn']:p for p in primes}
    views['prime_targets']=primes
    for p in primes:
        if p['supplier_abn'] not in supplier_by_abn:
            supplier_by_abn[p['supplier_abn']]=dict(organisation=p['organisation'],supplier_abn=p['supplier_abn'],original_awarded_value=None,source_url=p['source_url'])
    for s in supplier_by_abn.values():
        if s['supplier_abn'] not in prime_by_abn and (s['original_awarded_value'] is None or float(s['original_awarded_value'])<config['private_supplier_min_awarded_value_36m']):continue
        r=add(s,'private','abn:'+s['supplier_abn'])
        r['features']['category_spend']=supplier_category_spend.get(s['supplier_abn'])
        r['inputs'].update(supplier_value=float(s['original_awarded_value']) if s['original_awarded_value'] is not None else None,ipp_prime=prime_by_abn.get(s['supplier_abn'],{}).get('active_high_value_contracts'))
    for p in rap['targets']:
        if p['period_status']!='within_published_period':continue
        r=add(p,'private','rap:'+p['record_key']);fields=p.get('approved_extraction') or {}
        def finding(key):
            value=fields.get(key,{}).get('value')
            return int(value) if type(value) is bool else None
        r['inputs'].update(rap_type={'Reflect':1,'Innovate':2,'Stretch':3,'Elevate':4}.get(p['rap_type']),plan_ending=int((p['period_end']-as_of).days<=365),procurement_target=finding('has_procurement_target'),employment_target=finding('employment_target'))
        required=fields.get('requires_supply_nation_certified',{}).get('value')
        if required=='yes':
            certification=profile.get('supply_nation_status')
            blocked=None if not certification else certification.strip().casefold()!='certified'
            r['penalty_inputs']['blocker']=None if blocked is None else int(blocked)
            r['blockers']=dict(status='unknown' if blocked is None else 'blocked' if blocked else 'requirements_met',gaps=['Supply Nation certified'] if blocked else [],unknown=['Supply Nation certification'] if blocked is None else [])
        # RAP identity proposals are deliberately not used to combine ABN supplier scores.
    state_groups=defaultdict(list)
    for s in state['contracts']:
        if s.get('agency') and s.get('state')=='WA':state_groups[s['agency']].append(s)
    for agency,records in sorted(state_groups.items()):
        key='state:'+hashlib.sha256(('WA|'+agency).encode()).hexdigest()
        r=add(dict(account=agency,state='WA',source_url=records[0]['source_url'],why_now='WA FY2021-24 historical observations only; verify current buying',target_roles=sorted({s['category_tag'] for s in records})),'state',key)
        values=[s.get('value') for s in records]
        r['inputs'].update(historical_value=sum(float(v) for v in values) if all(v is not None for v in values) else None,historical_frequency=len(records))
    for h in target['healthcare_accounts']:
        r=add(h,'healthcare','health:'+h['source']+':'+h['record_id'])
        r['inputs'].update(size={'small':1,'medium':2,'large':3}.get(str(h.get('size') or '').casefold()),rap=1 if current_rap(h['organisation']) else None)
    quality=[dict(r) for r in conn.execute(text('''SELECT * FROM market.v_aged_care_quality WHERE reference_period<=:period
        AND (staffing_rating<=2 OR overall_rating<=2) ORDER BY row_identity'''),dict(period=as_of.strftime('%Y-%m'))).mappings()]
    for h in quality:
        r=add(dict(h,account=h['service_name'] or h['provider_name'],why_now='Low published aged-care staffing or overall rating; need is a research signal, not hiring intent',target_roles=['Care workforce lead','Registered nursing lead']),'healthcare','aged:'+h['row_identity'])
        r['inputs'].update(staffing_need=5-h['staffing_rating'] if h['staffing_rating'] is not None else None,quality=5-h['overall_rating'] if h['overall_rating'] is not None else None,size={'small':1,'medium':2,'large':3}.get(str(h['size'] or '').casefold()),rap=1 if current_rap(h['provider_name']) else None)
    # Compare published forward signals against a retained pre-week generation.
    # A missing baseline stays a coverage gap rather than labelling all plans new.
    previous=conn.execute(text('''SELECT id FROM market.view_generations WHERE as_of<=:since
        AND EXISTS(SELECT 1 FROM market.view_rows WHERE generation_id=id AND view_name='forward_pipeline')
        ORDER BY as_of DESC,generated_at DESC,id DESC LIMIT 1'''),dict(since=as_of-timedelta(days=7))).scalar()
    if previous:
        old={}
        for stored in conn.execute(text("SELECT row FROM market.view_rows WHERE generation_id=:id AND view_name='forward_pipeline'"),dict(id=previous)):
            r=stored[0];old[(r.get('source'),r.get('source_id'))]=r
        for signal in pipeline['signals']:
            if signal['signal']!='published_plan':continue
            prior=old.get((signal.get('source'),signal.get('source_id')))
            keys=('title','buyer','expected_from','expected_to','status','category_tag')
            if prior is None or any(str(prior.get(k))!=str(signal.get(k)) for k in keys):
                delta['changes'].append(dict(source=signal['source'],record_key=signal['source_id'],organisation=signal['buyer'],
                    event='new_published_plan' if prior is None else 'published_plan_changed',source_url=signal.get('source_url')))
    else:delta['baseline_gaps'].append(dict(source='forward_pipeline',status='no retained pre-week view generation'))
    for signal in pipeline['signals']:
        if signal['signal'].startswith('recorded_contract_end') and as_of<=signal['date']<=as_of+timedelta(days=7):
            delta['changes'].append(dict(source='federal_contracts',record_key=signal['source_id'],organisation=signal['buyer'],
                event='recorded_contract_end_within_week; extensions_possible',source_url=signal.get('source_url')))
    account_rows=list(accounts.values())
    similar=lookalikes(account_rows,client_keys)
    for row,match in zip(account_rows,similar):
        row['inputs']['lookalike']=match['similarity'];row['lookalike_basis']=match
        row['basis']['lookalike']='No past-client comparison with known dimensions' if match['similarity'] is None else 'Recorded past client '+match['matched_client_key']+'; dimension coverage '+str(match['similarity_coverage_pct'])+'%; missing dimensions retain weight'
    views.update(account_score=score_accounts(account_rows,config),lookalikes=similar,
                 partner_candidates=target['partner_candidates'],indigenous_partners=target['indigenous_corporations'],rap_targets=rap['targets'],
                 panel_access=panels['panels'],forward_pipeline=pipeline['signals'],demand_vs_bench=[dict(r,lane='private',why_now='Regional role demand versus aggregate bench; no account attribution',target_roles=[r['role_family']]) for r in bench['rows']],
                 whats_changed=[dict(r,why_now='Retained evidence '+r['event']+' since '+str(as_of-timedelta(days=7)),lane='state' if r['source']=='state_contracts' else 'private' if r['source'].startswith('rap') else 'federal') for r in delta['changes']])
    metrics=dict(source_limitations={name:r.get('limitations',r.get('warnings',[])) for name,r in [('federal',federal),('buyict',ict),('ipp',ipp),('targets',target),('rap',rap),('state',state),('panels',panels),('forward',pipeline),('bench',bench),('changes',delta)]},
                 baseline_gaps=delta['baseline_gaps'],skipped=['v_grant_triggers: no grant input data'],healthcare_quality_rows=len(quality),private_prime_rows_omitted_without_safe_register_name=len(ipp['mmr_primes'])-len(primes))
    return views,metrics


def publish_generation(conn, as_of, views, metrics, config_sha256, code_bundle=None):
    """Caller owns transaction; all writes, retention and pointer changes are atomic."""
    generation=uuid.uuid4();counts={};records=[]
    if set(views)!=set(VIEW_NAMES):raise ValueError('Incomplete view generation')
    for name,rows in views.items():
        selected=rows[:ROW_LIMIT];lane_counts=None
        if name=='account_score':
            selected=[];lane_counts={lane:dict(total=0,stored=0,cap=ROW_LIMIT//4) for lane in ('federal','state','private','healthcare')}
            for row in rows:
                lane=lane_counts[row['lane']];lane['total']+=1
                if lane['stored']<lane['cap']:selected.append(row);lane['stored']+=1
        counts[name]=dict(total=len(rows),stored=len(selected),cap=ROW_LIMIT)
        if lane_counts is not None:counts[name]['lanes']=lane_counts
        for position,row in enumerate(selected,1):
            shaped=shape_row(row,position=position)
            records.append(dict(generation=generation,name=name,position=position,row=json.dumps(shaped,default=json_default,allow_nan=False)))
    metrics=dict(metrics,row_counts=counts)
    conn.execute(text('''INSERT INTO market.view_generations(id,as_of,status,code_bundle,config_sha256,metrics)
        VALUES(:id,:asof,'complete',:bundle,:sha,CAST(:metrics AS jsonb))'''),dict(id=generation,asof=as_of,bundle=code_bundle,sha=config_sha256,metrics=json.dumps(metrics,default=json_default)))
    for start in range(0,len(records),1000):
        conn.execute(text('''INSERT INTO market.view_rows(generation_id,view_name,position,row)
            VALUES(:generation,:name,:position,CAST(:row AS jsonb))'''),records[start:start+1000])
    conn.execute(text('''INSERT INTO market.current_view_generation(generation_id) VALUES(:id)
        ON CONFLICT(singleton) DO UPDATE SET generation_id=EXCLUDED.generation_id'''),dict(id=generation))
    conn.execute(text('''DELETE FROM market.view_rows WHERE generation_id IN
        (SELECT id FROM market.view_generations ORDER BY generated_at DESC,id DESC OFFSET 30)
        AND generation_id<>(SELECT generation_id FROM market.current_view_generation)'''))
    return dict(status='complete',generation_id=str(generation),as_of=str(as_of),metrics=metrics)


def refresh_views(engine, as_of):
    if type(as_of) is not date:raise ValueError('Explicit as-of date required')
    config_bytes=CONFIG.read_bytes();config=validate_config(json.loads(config_bytes));sha=hashlib.sha256(config_bytes).hexdigest()
    manifest=Path(__file__).parents[1]/'source-manifest.json'
    bundle=json.loads(manifest.read_text()).get('code_bundle_sha256') if manifest.exists() else None
    # Serialise publishers before opening the repeatable-read snapshot. A waiting
    # publisher must see a fresh snapshot after its predecessor commits.
    with engine.connect() as lock:
        lock.execute(text("SELECT pg_advisory_lock(hashtext('market:spec_views'))"));lock.commit()
        try:
            with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
                views,metrics=build_view_rows(conn,as_of,config)
                metrics['scoring_config']=config
                return publish_generation(conn,as_of,views,metrics,sha,bundle)
        finally:
            lock.execute(text("SELECT pg_advisory_unlock(hashtext('market:spec_views'))"));lock.commit()
