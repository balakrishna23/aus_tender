"""Contact-free aggregate business inputs; named changes, immutable snapshots and strict columns."""
import csv
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path

from sqlalchemy import text

from .parsers import EMAIL_PATTERN,SourceError
from .resources import https_url
from .reporting import write_report_files

INPUT_FIELDS={
 'bench':['role_family','state','clearance','count','as_at'],
 'role_map':['classification','occupation_code','occupation_title','role_family'],
 'relationships':['buyer_key','relationship_status','last_activity_date'],
 'invitations':['buyer_key','opportunity_id','invitation_status','invitation_date'],
 'outcomes':['buyer_key','opportunity_id','category','submitted_date','outcome','offered_rate','rate_unit'],
 'lineage':['predecessor_id','successor_id','effective_date','change_type'],
}
STATES={'ACT','NSW','NT','QLD','SA','TAS','VIC','WA','AUST'}


def input_rows(kind,path,as_of):
    if kind not in INPUT_FIELDS:raise ValueError('Unknown business input kind')
    with Path(path).open(encoding='utf-8-sig',newline='') as stream:
        reader=csv.DictReader(stream)
        if set(reader.fieldnames or [])!=set(INPUT_FIELDS[kind]):raise SourceError('Input columns must exactly match the contact-free template')
        rows=[];identities=set()
        for row in reader:
            if None in row or any(v is None for v in row.values()):raise SourceError('Malformed CSV input')
            row={k:v.strip() for k,v in row.items()}
            if not all(row.values()) or EMAIL_PATTERN.search(json.dumps(row)):raise SourceError('Blank required value or contact data in aggregate input')
            for field in ['as_at','last_activity_date','invitation_date','submitted_date','effective_date']:
                if field in row:
                    day=date.fromisoformat(row[field])
                    if day>as_of:raise SourceError('Business observation cannot be in the future')
            if kind=='bench':
                if row['state'] not in STATES or row['clearance'] not in ['none','Baseline','NV1','NV2','unknown']:raise SourceError('Unknown bench region or clearance')
                try:count=int(row['count'])
                except ValueError:raise SourceError('Bench count must be a nonnegative integer') from None
                if count<0 or str(count)!=row['count']:raise SourceError('Bench count must be a nonnegative integer')
                row['count']=count;identity=tuple(row[k] for k in ['role_family','state','clearance'])
            elif kind=='role_map':identity=tuple(row[k] for k in ['classification','occupation_code','occupation_title'])
            elif kind=='relationships':
                if row['relationship_status'] not in ['unknown','researching','active','past_client','do_not_pursue']:raise SourceError('Unknown relationship status')
                identity=(row['buyer_key'],)
            elif kind=='invitations':
                if row['invitation_status'] not in ['invited','not_invited','unknown']:raise SourceError('Unknown invitation status')
                identity=(row['buyer_key'],row['opportunity_id'])
            elif kind=='outcomes':
                if row['outcome'] not in ['submitted','won','lost','withdrawn','no_bid','unknown']:raise SourceError('Unknown bid outcome')
                if row['rate_unit'] not in ['hour','day','year','unknown']:raise SourceError('Unknown rate unit')
                from .demand import number
                if number(row['offered_rate']) is None or number(row['offered_rate'])<0:raise SourceError('Rate must be a nonnegative number')
                identity=(row['buyer_key'],row['opportunity_id'])
            else:
                if row['predecessor_id']==row['successor_id']:raise SourceError('Lineage cannot point to itself')
                if row['change_type'] not in ['rename','merge','split','transfer']:raise SourceError('Unknown lineage change')
                identity=tuple(row[k] for k in ['predecessor_id','successor_id','effective_date','change_type'])
            if identity in identities:raise SourceError('Duplicate business input identity')
            identities.add(identity);row['record_key']=hashlib.sha256(json.dumps(identity).encode()).hexdigest();rows.append(row)
    if not rows:raise SourceError('No business observations; blank template is not data')
    return rows


def import_input(engine,kind,path,source_url,as_of,reviewer):
    from .research import import_research,SCHEMAS
    if not reviewer or not reviewer.strip():raise ValueError('A named input owner is required')
    https_url(source_url)
    rows=input_rows(kind,path,as_of)
    # Reuse the same snapshot machinery without accepting additional raw fields.
    SCHEMAS[kind]=set(INPUT_FIELDS[kind])|{'record_key','source_url'}
    for row in rows:row['source_url']=source_url
    bundle=dict(source=kind,observed_at=datetime.now(timezone.utc).isoformat(),contains_contact_data=False,
                provenance=dict(source_url=source_url,reviewer=reviewer,as_of=str(as_of),licence='Private Illoura input; internal use only',
                                input_sha256=hashlib.sha256(Path(path).read_bytes()).hexdigest()),records=rows)
    # Content-addressed private staging file alongside the input; never replace a prior evidence file.
    data=json.dumps(bundle,sort_keys=True,ensure_ascii=False,indent=2)
    destination=Path(path).with_name(Path(path).name+'.'+hashlib.sha256(data.encode()).hexdigest()[:16]+'.evidence.json')
    with destination.open('x',encoding='utf-8') as stream:stream.write(data)
    return import_research(engine,destination)


def templates(directory):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    for kind,fields in INPUT_FIELDS.items():
        path=directory/(kind+'.csv')
        with path.open('x',encoding='utf-8',newline='') as stream:csv.writer(stream).writerow(fields)
    return {'templates':list(INPUT_FIELDS),'directory':str(directory)}


def build_bench(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        inputs=[dict(r) for r in conn.execute(text("SELECT * FROM market.current_research WHERE source IN ('bench','role_map')")).mappings()]
        demand=[dict(r) for r in conn.execute(text("SELECT * FROM market.current_demand WHERE source='jsa_ivi' AND region<>'AUST' AND period<=:asof"),{'asof':as_of}).mappings()]
    bench=[r['payload'] for r in inputs if r['source']=='bench'];maps={}
    for r in inputs:
        if r['source']=='role_map':
            p=r['payload'];maps[(p['classification'],p['occupation_code'],p['occupation_title'])]=p['role_family']
    latest={}
    for r in demand:
        key=(r['classification'],r['occupation_code'],r['occupation_title'],r['region'])
        if key not in latest or r['period']>latest[key]['period']:latest[key]=r
    # Demand has no clearance dimension; never repeat an advertisement count for every clearance.
    groups={}
    for key,r in latest.items():
        role=maps.get(key[:3])
        if not role:continue
        group=groups.setdefault((role,r['region']),dict(role_family=role,state=r['region'],ads_3m_average=0,unknown_components=0,occupations=0,period=r['period']))
        group['occupations']+=1
        if r['value'] is None:group['unknown_components']+=1
        else:group['ads_3m_average']+=r['value']
    rows=[]
    for (role,state),group in groups.items():
        matching=[r for r in bench if r['role_family']==role and r['state']==state and date.fromisoformat(r['as_at'])<=as_of]
        group.update(bench_count=sum(r['count'] for r in matching) if matching else None,
                     bench_as_at=min(r['as_at'] for r in matching) if matching else None,
                     clearance_counts={r['clearance']:r['count'] for r in matching},clearance_demand='unknown',
                     bench_needs_refresh=any((as_of-date.fromisoformat(r['as_at'])).days>30 for r in matching) if matching else None,
                     coverage='partial demand values' if group['unknown_components'] else 'mapped occupations only')
        rows.append(group)
    return dict(as_of=as_of,status='aggregate research; empty until business mappings and bench supplied',rows=rows,
        unmapped_occupation_series=sum(key[:3] not in maps for key in latest),bench_records=len(bench),
        limitations=['Mappings are explicit reviewed occupation/classification/title entries; no cross-edition inference.',
                     'Counts of available candidates and online advertisements measure different things; no fill-rate or revenue estimate is calculated.',
                     'Demand has no clearance data. Bench clearance counts are shown separately.',
                     'Missing bench data remains unknown. National and state rows must not be added together.'])


def write_bench(report,directory):
    return write_report_files(report,directory,'demand_vs_bench',report['rows'],
        ['role_family','state','period','ads_3m_average','unknown_components','bench_count','bench_as_at','coverage'],'Demand and candidate bench')
