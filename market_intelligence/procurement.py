"""Stream contact-free procurement snapshots into PostgreSQL atomically."""
from collections import Counter
from contextlib import closing
from datetime import date,datetime
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
import uuid

from sqlalchemy import text

from .extract_procurement import CONTRACT_FIELDS, OPTIONAL_CONTRACT_FIELDS, DERIVED_CONTRACT_FIELDS, PANEL_FIELDS
from .ingest import file_hash
# 2: the payload also keeps procurement-method facts and category tags when the export has them.
PARSER_REVISION='2'


def buyer_key(name, abn):
    import hashlib
    return hashlib.sha256(((name or '')+'\0'+('' if abn is None else str(abn))).encode()).hexdigest()


def source_date(value, issues, field):
    if value is None or not str(value).strip():
        return None
    try:
        return datetime.fromisoformat(str(value).strip().replace('Z','+00:00')).date()
    except ValueError:
        issues.append('invalid_'+field)
        return None


def amount(value, issues, field):
    if value is None or not str(value).strip():
        return None
    try:
        result=Decimal(str(value))
        if not result.is_finite() or result<0:
            raise InvalidOperation()
        return result
    except InvalidOperation:
        issues.append('invalid_'+field)
        return None


def panel_period(value):
    if not value:
        return None,None
    match=re.fullmatch(r'\s*(\d{1,2}-[A-Za-z]{3}-\d{4})\s+to\s+(\d{1,2}-[A-Za-z]{3}-\d{4})\s*',value)
    if not match:
        return None,None
    try:
        months={name:index for index,name in enumerate(['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'],1)}
        dates=[]
        for value in match.groups():
            day,month,year=value.split('-')
            dates.append(date(int(year),months[month.lower()],int(day)))
        start,end=dates
        return (start,end) if end>=start else (None,None)
    except (ValueError,KeyError):
        return None,None


def contract_values(snapshot_id, row):
    issues=[]
    number=row['cn_id']
    if not number or not re.fullmatch(r'CN[0-9]+(?:-A[0-9]+)?',number):
        raise ValueError('Contract export lacks a valid notice identity')
    if '-A' in number:
        issues.append('legacy_amendment_identity_requires_review')
    name=row['buyer'] or ''
    if not name.strip():issues.append('missing_buyer')
    original=amount(row['original_value'],issues,'original_value')
    current=amount(row['current_value'],issues,'current_value')
    dates=[source_date(row[k],issues,k) for k in ['start_date','current_end_date','first_published']]
    return (snapshot_id,number,buyer_key(name,row['buyer_abn']),name,row['buyer_abn'],row['supplier'],row['supplier_abn'],
            row['category_family'],original,current,*dates,row['page_url'] or row['source_url'],row['retrieved_at'],
            json.dumps(row),json.dumps(issues)),issues


def import_procurement(engine, database):
    path=Path(database).resolve(strict=True)
    run_id=uuid.uuid4()
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,'procurement','running')"),{'id':run_id})
    try:
        digest=file_hash(path)
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as origin:
            origin.row_factory=sqlite3.Row
            origin.execute('PRAGMA query_only=ON');origin.execute('BEGIN')
            metadata=json.loads(origin.execute('SELECT payload FROM export_metadata').fetchone()[0])
            if metadata.get('contains_contact_data') is not False or metadata.get('source_access')!='read_only_transactions':
                raise ValueError('Expected a contact-free read-only procurement export')
            exported=datetime.fromisoformat(metadata['exported_at'])
            if exported.tzinfo is None:raise ValueError('Export time needs a timezone')
            count=origin.execute('SELECT count(*) FROM contracts').fetchone()[0]
            if not count or count!=metadata['sources']['contract_api']['rows']:
                raise ValueError('Contract export row-count reconciliation failed')
            metrics=Counter(contracts=count)
            with engine.begin() as conn:
                conn.execute(text('SELECT pg_advisory_xact_lock(191126004)'))
                existing=conn.execute(text('SELECT id FROM market.procurement_snapshots WHERE sha256=:hash AND parser_revision=:revision'),{'hash':digest,'revision':PARSER_REVISION}).scalar()
                if existing:
                    snapshot_id,status=existing,'unchanged'
                else:
                    snapshot_id,status=uuid.uuid4(),'complete'
                    conn.execute(text('INSERT INTO market.procurement_snapshots(id,sha256,exported_at,metadata,parser_revision) VALUES(:id,:hash,:exported,CAST(:metadata AS jsonb),:revision)'),
                                 dict(id=snapshot_id,hash=digest,exported=exported,metadata=json.dumps(metadata),revision=PARSER_REVISION))
                    raw=conn.connection.driver_connection
                    with raw.cursor() as cursor:
                        with cursor.copy('''COPY market.procurement_contracts(snapshot_id,cn_id,buyer_key,buyer,buyer_abn,supplier,supplier_abn,
                            category_family,original_value,current_value,start_date,current_end_date,first_published,source_url,retrieved_at,payload,issues) FROM STDIN''') as output:
                            present={r[1] for r in origin.execute('PRAGMA table_info(contracts)')}
                            columns=CONTRACT_FIELDS+[f for f in OPTIONAL_CONTRACT_FIELDS+DERIVED_CONTRACT_FIELDS if f in present]
                            for item in origin.execute('SELECT '+','.join(columns)+' FROM contracts'):
                                values,issues=contract_values(snapshot_id,dict(item))
                                metrics.update(issues)
                                output.write_row(values)
                        seen=set()
                        with cursor.copy('COPY market.procurement_panel_links(snapshot_id,cn_id,son_id,source_url,retrieved_at) FROM STDIN') as output:
                            for item in origin.execute('SELECT cn_id,son_id,url,scraped_at FROM contract_panel_links'):
                                number=re.sub(r'-A[0-9]+$','',item['cn_id'] or '')
                                for son in re.findall(r'\bSON[0-9]+\b',item['son_id'] or ''):
                                    key=(number,son,item['url'] or '')
                                    if not re.fullmatch(r'CN[0-9]+',number) or key in seen:continue
                                    seen.add(key)
                                    output.write_row((snapshot_id,*key,item['scraped_at']))
                                    metrics['panel_links']+=1
                        with cursor.copy('''COPY market.procurement_panels(snapshot_id,observation_id,son_id,title,owner_agency,category,
                            period_text,start_date,current_expiry,source_url,retrieved_at,date_basis) FROM STDIN''') as output:
                            for index,item in enumerate(origin.execute('SELECT '+','.join(PANEL_FIELDS)+' FROM panels'),1):
                                if not re.fullmatch(r'SON[0-9]+',item['son_id'] or ''):
                                    metrics['invalid_panel_id']+=1;continue
                                start,end=panel_period(item['standing_offer_period'])
                                if end is None:metrics['panel_dates_unknown']+=1
                                output.write_row((snapshot_id,index,item['son_id'],item['title'] or item['son_id'],item['agency'],item['primary_category'],
                                                  item['standing_offer_period'],start,end,item['url'],item['scraped_at'],
                                                  'notice_period; extensions_and_intakes_unverified' if end else 'unknown; needs_manual_check'))
                                metrics['panels']+=1
                    if file_hash(path)!=digest:raise ValueError('Procurement export changed during import')
                    conn.execute(text('''INSERT INTO market.current_procurement_snapshot(snapshot_id) VALUES(:id)
                        ON CONFLICT(singleton) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
                        WHERE (SELECT (exported_at,imported_at) FROM market.procurement_snapshots WHERE id=EXCLUDED.snapshot_id)>
                              (SELECT (exported_at,imported_at) FROM market.procurement_snapshots WHERE id=market.current_procurement_snapshot.snapshot_id)'''),{'id':snapshot_id})
                conn.execute(text('''UPDATE market.pipeline_runs SET status=:status,finished_at=now(),snapshot_id=:snapshot,
                    metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),dict(status=status,snapshot=snapshot_id,metrics=json.dumps(metrics),id=run_id))
        return dict(status=status,snapshot_id=str(snapshot_id),metrics=dict(metrics))
    except Exception as exc:
        with engine.begin() as conn:
            conn.execute(text("UPDATE market.pipeline_runs SET status='failed',finished_at=now(),error_code=:error WHERE id=:id"),{'id':run_id,'error':type(exc).__name__})
        raise
