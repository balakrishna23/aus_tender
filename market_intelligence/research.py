"""Import strictly whitelisted retained evidence; produce scoped RAP and state reports."""
import calendar
from datetime import date,datetime,timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from sqlalchemy import text

from .ingest import file_hash
from .parsers import EMAIL_PATTERN,SourceError,name_key,valid_abn
from .reporting import write_report_files
from .resources import https_url

SCHEMAS={
 'rap_directory':{'record_key','organisation','rap_type','period_as_published','document_url','source_url','document_sha256','collection_date','licence'},
 'rap_commitments':{'record_key','organisation','reviewed_on','action','pdf_page','summary','timeline','source_url','period_start','period_end',
                    'candidate_abns','document_sha256','review_method','entity_scope','target_completion'},
 'state_contracts':{'record_key','state','source_file','row_no','agency','title','reference','procurement_method','unspsc','award_date','start_date',
                    'end_date','value','supplier','supplier_abn','panel','loaded_at','source_url','licence','source_modified','category_tag','category_rule'}
}


def import_research(engine,path):
    path=Path(path).resolve(strict=True);file_sha=file_hash(path);data=json.loads(path.read_text(encoding='utf-8'))
    # Acquisition time may change on a repeated export; unchanged evidence remains one content snapshot.
    sha=hashlib.sha256(json.dumps({k:data[k] for k in ['source','provenance','records']},sort_keys=True).encode()).hexdigest()
    source=data['source']
    if source in ['rap_directory','rap_commitments']:
        # Privacy clearance is part of the snapshot identity, allowing safe reimport of legacy exports.
        sha=hashlib.sha256((sha+json.dumps(data.get('privacy_review'),sort_keys=True)).encode()).hexdigest()
    if source not in SCHEMAS or data.get('contains_contact_data') is not False:raise SourceError('Unrecognised research export')
    records=data['records']
    if not records or len({r['record_key'] for r in records})!=len(records):raise SourceError('Empty or duplicate research export')
    observed=datetime.fromisoformat(data['observed_at'])
    if observed.tzinfo is None or observed>datetime.now(timezone.utc):raise SourceError('Invalid research observation time')
    for row in records:
        if set(row)!=SCHEMAS[source] or EMAIL_PATTERN.search(json.dumps(row)):raise SourceError('Research export contains unsupported fields or contact data')
        https_url(row['source_url'])
        if source in ['rap_directory','rap_commitments']:
            from .rap_extract import privacy_check,public_url,reviewer_id,digest
            clearance=data.get('privacy_review',{})
            if set(clearance)!={'reviewer_id','contains_personal_data','payload_sha256'}:raise SourceError('Unsupported RAP privacy review fields')
            reviewer_id(clearance.get('reviewer_id'))
            if clearance.get('contains_personal_data') is not False or clearance.get('payload_sha256')!=digest({'records':records,'provenance':data['provenance']}):
                raise SourceError('RAP records require exact human privacy clearance')
            public_url(row['source_url'])
            for k,v in row.items():
                if isinstance(v,str) and k not in ['source_url','document_url','document_sha256']:privacy_check(v)
        if source=='rap_directory':
            if row['rap_type'] not in ['Reflect','Innovate','Stretch','Elevate']:raise SourceError('Unexpected RAP type')
            date.fromisoformat(row['collection_date'])
            if row['document_url']:public_url(row['document_url'])
        if source=='rap_commitments':
            if not isinstance(row['pdf_page'],int) or row['pdf_page']<1:raise SourceError('RAP page citation required')
            for field in ['reviewed_on','period_start','period_end']:date.fromisoformat(row[field])
            for abn in row['candidate_abns']:valid_abn(abn)
        doc_sha=row.get('document_sha256')
        if doc_sha is not None and not re.fullmatch('[0-9a-f]{64}',doc_sha):raise SourceError('Invalid document checksum')
    if file_hash(path)!=file_sha:raise SourceError('Research export changed while reading')
    run=uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text('SELECT pg_advisory_xact_lock(hashtext(:source))'),{'source':'market:research:'+source})
        existing=conn.execute(text('SELECT id FROM market.research_snapshots WHERE source=:source AND sha256=:sha'),{'source':source,'sha':sha}).scalar()
        if existing:return dict(status='unchanged',snapshot_id=str(existing),rows=len(records))
        snapshot=uuid.uuid4()
        conn.execute(text('''INSERT INTO market.research_snapshots(id,source,sha256,observed_at,provenance,row_count)
            VALUES(:id,:source,:sha,:observed,CAST(:provenance AS jsonb),:count)'''),dict(id=snapshot,source=source,sha=sha,observed=observed,
            provenance=json.dumps(dict(data['provenance'],rap_privacy_review=data['privacy_review']) if source in ['rap_directory','rap_commitments'] else data['provenance']),count=len(records)))
        for start in range(0,len(records),4000):
            conn.execute(text('INSERT INTO market.research_records(snapshot_id,record_key,payload) VALUES(:snapshot,:key,CAST(:payload AS jsonb))'),
                [dict(snapshot=snapshot,key=r['record_key'],payload=json.dumps(r)) for r in records[start:start+4000]])
        # Re-clearing the same retained RAP observation must replace the uncleared pointer.
        comparison='>=' if source in ['rap_directory','rap_commitments'] else '>'
        conn.execute(text(f'''INSERT INTO market.current_research_snapshots(source,snapshot_id) VALUES(:source,:id)
            ON CONFLICT(source) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
            WHERE (SELECT observed_at FROM market.research_snapshots WHERE id=EXCLUDED.snapshot_id){comparison}
                  (SELECT observed_at FROM market.research_snapshots WHERE id=market.current_research_snapshots.snapshot_id)'''),dict(source=source,id=snapshot))
        conn.execute(text('''INSERT INTO market.pipeline_runs(id,source,status,snapshot_id,finished_at,metrics)
            VALUES(:id,:source,'complete',:snapshot,now(),CAST(:metrics AS jsonb))'''),dict(id=run,source=source,snapshot=snapshot,metrics=json.dumps({'rows':len(records)})))
    return dict(status='complete',snapshot_id=str(snapshot),rows=len(records))


def period_range(value):
    """Published month/year periods only; unclear periods stay unknown."""
    found=re.fullmatch(r'([A-Za-z]+) (\d{4}) to ([A-Za-z]+) (\d{4})',value.strip())
    if not found:return None,None
    try:
        start=datetime.strptime(found[1]+' '+found[2],'%B %Y').date()
        end=datetime.strptime(found[3]+' '+found[4],'%B %Y').date()
        end=end.replace(day=calendar.monthrange(end.year,end.month)[1])
    except ValueError:return None,None
    return (start,end) if start<=end else (None,None)


def build_rap(engine,as_of):
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        records=[dict(r) for r in conn.execute(text("SELECT * FROM market.current_research WHERE source='rap_directory' AND provenance->'rap_privacy_review'->>'contains_personal_data'='false'")).mappings()]
        # A matching legal name only proposes a candidate ABN. It never establishes the scope of a group RAP.
        names={}
        for r in conn.execute(text('SELECT DISTINCT abn,name_key FROM market.current_register_entities WHERE NOT is_individual AND abn IS NOT NULL AND name_key IS NOT NULL')):
            names.setdefault(r.name_key,set()).add(r.abn)
        contracts={r['supplier_abn']:dict(r) for r in conn.execute(text('''SELECT supplier_abn,count(*) AS contracts,sum(original_value) AS original_award_value
            FROM market.current_analytical_contracts WHERE supplier_abn IS NOT NULL GROUP BY supplier_abn''')).mappings()}
        approved={r['document_sha256']:dict(r) for r in conn.execute(text('SELECT * FROM market.current_approved_rap_extractions')).mappings()}
        certification=conn.execute(text("SELECT value FROM market.illoura_profile WHERE key='supply_nation_status'")).scalar()
    directory=records
    out=[]
    for row in directory:
        p=row['payload'];start,end=period_range(p['period_as_published']);key=name_key(p['organisation'])
        # Legacy manual commitments lack an accountable exact-document approval event.
        # Keep them out of reports; only the reviewed extraction view supplies findings.
        same=[]
        abns=set(names.get(key,set()))
        active=bool(start and start<=as_of<=end)
        renewing=bool(active and (end-as_of).days<=365)
        proposed=[dict(abn=a,**contracts[a]) for a in sorted(abns) if a in contracts]
        points={'rap_type':{'Reflect':5,'Innovate':10,'Stretch':15,'Elevate':20}[p['rap_type']] if active else 0,
                'reviewed_commitments':min(len(same),5)*5 if active else 0,'renewal_window':15 if renewing else 0,
                'candidate_government_supplier':10 if active and proposed else 0}
        extracted=approved.get(p['document_sha256']);extracted_fields=extracted['fields'] if extracted else {}
        certified_required=extracted_fields.get('requires_supply_nation_certified',{}).get('value')
        blocked=(certification.strip().casefold()!='certified') if certified_required=='yes' and certification else None
        points['approved_procurement_target']=10 if active and extracted_fields.get('has_procurement_target',{}).get('value') is True else 0
        out.append(dict(p,lane='private',period_start=start,period_end=end,period_status='within_published_period' if active else 'expired' if end and end<as_of else 'future' if start and start>as_of else 'unknown',
            score=sum(points.values()),score_components=points,candidate_abns=sorted(abns),entity_match_status='candidate_only' if abns else 'unmatched',
            reviewed_commitments=same,candidate_supplier_context=proposed,commitment_count=len(same),
            why_now='Published RAP ends within 12 months' if renewing else 'Page-cited procurement commitment requires follow-up research' if active and same else 'Verify current plan and procurement relevance',
            target_roles='unknown; commitment category review required',delivery_shortfall='unknown',eligibility='unknown; profile and specific requirements need review',
            approved_extraction=extracted_fields,approved_extraction_run_id=str(extracted['id']) if extracted else None,
            approved_extraction_provenance=dict(document_sha256=extracted['document_sha256'],source_url=extracted['source_url'],
                licence=extracted['licence'],pages=extracted['pages'],**extracted['provenance']) if extracted else None,
            blocked_until_certified=blocked,
            provenance_snapshot_id=str(row['snapshot_id'])))
    out.sort(key=lambda r:(-r['score'],r['organisation'],r['record_key']))
    for rank,r in enumerate(out,1):r['rank']=rank
    return dict(as_of=as_of,status='draft RAP research; no missed targets inferred',targets=out,
        summary=dict(directory_entries=len(out),reviewed_commitments=sum(len(r['reviewed_commitments']) for r in out),
                     entries_with_same_document_evidence=sum(bool(r['reviewed_commitments']) for r in out),
                     entries_with_candidate_abns=sum(bool(r['candidate_abns']) for r in out)),
        limitations=['Directory snapshot collected 27 October 2026; this report does not assert it is the latest list.',
                     'Published RAP commitments are not proof of unspent budget, missed targets, supplier acceptance or eligibility.',
                     'Legal-name matches are proposals; a group RAP may cover several legal entities.',
                     'Findings require a registered reviewer approval for the exact document hash; legacy manual findings and uncleared directory snapshots are excluded.',
                     'Expired and future plans receive no current priority points. Role/category, results and certification requirements remain unassessed.',
                     'Internal research only; source copyright retained and commercial redistribution rights are not established.'])


def build_state(engine,as_of):
    with engine.connect() as conn:
        rows=[dict(r['payload'],provenance_snapshot_id=str(r['snapshot_id'])) for r in conn.execute(text("SELECT * FROM market.current_research WHERE source='state_contracts'")).mappings()]
    if not rows:raise ValueError('No retained state-contract export imported')
    relevant=[dict(r,lane='state',why_now='Historical supplier and category research; verify current procurement',target_roles=r['category_tag'])
              for r in rows if r['category_tag']!='other']
    return dict(as_of=as_of,status='historical retained state contracts; not current market coverage',contracts=relevant,
        summary=dict(rows=len(rows),relevant_rows=len(relevant),states=sorted({r['state'] for r in rows})),
        limitations=['Retained source covers WA financial years 2021-22, 2022-23 and 2023-24 only; other states and recent awards are not covered.',
                     'Rows are source observations, not confirmed unique awards; reference numbers and suppliers may repeat.',
                     'Values do not distinguish original and amended awards; no current addressable-spend estimate is inferred.',
                     'Buyer identities, panel access and supplier entity scope require review. Descriptions and addresses are not imported.'])


def write_rap(report,directory):
    return write_report_files(report,directory,'rap_targets',report['targets'],
        ['rank','organisation','rap_type','period_status','period_end','score','commitment_count','entity_match_status','why_now'],'RAP research targets')


def write_state(report,directory):
    return write_report_files(report,directory,'state_contracts',report['contracts'],
        ['state','agency','award_date','category_tag','supplier','value','source_url'],'Historical state-contract research')
