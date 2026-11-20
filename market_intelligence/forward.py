"""Plans, open notices and ending contracts stay distinct research signals."""
from contextlib import closing
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

from sqlalchemy import text

from .categories import tag
from .ingest import file_hash
from .parsers import EMAIL_PATTERN,SourceError,name_key,website
from .reporting import write_report_files

TAGS=['ict_labour','ict_services','labour_hire_general','recruitment','healthcare_staffing','nursing_agency']


def import_forward(engine,database):
    path=Path(database).resolve(strict=True);sha=file_hash(path);rows=[]
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        db.execute('PRAGMA query_only=ON');db.execute('BEGIN');db.row_factory=sqlite3.Row
        meta=json.loads(db.execute('SELECT payload FROM metadata').fetchone()[0])
        if meta.get('contains_contact_data') is not False or meta.get('source_access')!='read_only_transaction':
            raise SourceError('Unverified source export')
        exported=datetime.fromisoformat(meta['exported_at'])
        if exported.tzinfo is None:raise SourceError('Export time needs timezone')
        for r in db.execute('SELECT * FROM opportunities'):
            r=dict(r)
            if EMAIL_PATTERN.search(json.dumps(r)):raise SourceError('Contact data in forward export')
            if not r['source'] or not r['source_id']:raise SourceError('Missing forward identity')
            missing_title=not bool(r['title'])
            issues=[];url=website(r['url'],issues)
            if not url or issues:raise SourceError('Invalid source URL')
            category,rule=tag(r.get('unspsc'),None,(r['title'] or '')+' '+(r.get('category') or ''))
            if category=='other' and r['source']=='BuyICT':category='ict_services';rule='BuyICT source; role category unverified'
            close=datetime.fromisoformat(r['closes_at']) if r.get('closes_at') else None
            if close and close.tzinfo is None:raise SourceError('Closing time needs timezone')
            observation_key=hashlib.sha256(json.dumps(r,sort_keys=True).encode()).hexdigest()
            rows.append(dict(observation_key=observation_key,source=r['source'],source_id=r['source_id'],kind=r['kind'],title=r['title'] or '[Title not published]',buyer=r['buyer'],
                buyer_name_key=name_key(r['buyer']),category_tag=category,expected_from=date.fromisoformat(r['expected_from']) if r.get('expected_from') else None,
                expected_to=date.fromisoformat(r['expected_to']) if r.get('expected_to') else None,closes_at=close,status=r['status_effective'],
                source_url=url,retrieved_at=r.get('retrieved_at'),payload=json.dumps({'category_rule':rule,'published':r.get('published'),'location':r.get('location'),
                    'issues':['missing_title'] if missing_title else []})))
    if not rows or len(rows)!=meta['rows']:raise SourceError('Forward export count mismatch or empty source')
    if file_hash(path)!=sha:raise SourceError('Source changed while reading')
    unique={r['observation_key']:r for r in rows}
    meta['exact_duplicate_rows_omitted']=len(rows)-len(unique)
    rows=list(unique.values())
    with engine.begin() as conn:
        conn.execute(text("SELECT pg_advisory_xact_lock(hashtext('market:forward'))"))
        old=conn.execute(text('SELECT id FROM market.forward_snapshots WHERE sha256=:s'),{'s':sha}).scalar()
        if old:return dict(status='unchanged',snapshot_id=str(old),rows=len(rows))
        snapshot=uuid.uuid4()
        conn.execute(text('''INSERT INTO market.forward_snapshots(id,sha256,exported_at,metadata,row_count)
            VALUES(:id,:sha,:exported,CAST(:meta AS jsonb),:rows)'''),dict(id=snapshot,sha=sha,exported=exported,meta=json.dumps(meta),rows=len(rows)))
        conn.execute(text('''INSERT INTO market.forward_observations(snapshot_id,observation_key,source,source_id,kind,title,buyer,buyer_name_key,
            category_tag,expected_from,expected_to,closes_at,status,source_url,retrieved_at,payload)
            VALUES(:snapshot_id,:observation_key,:source,:source_id,:kind,:title,:buyer,:buyer_name_key,:category_tag,:expected_from,:expected_to,
            :closes_at,:status,:source_url,:retrieved_at,CAST(:payload AS jsonb))'''),[dict(r,snapshot_id=snapshot) for r in rows])
        conn.execute(text('''INSERT INTO market.current_forward_snapshot(snapshot_id) VALUES(:id)
            ON CONFLICT(singleton) DO UPDATE SET snapshot_id=EXCLUDED.snapshot_id
            WHERE (SELECT exported_at FROM market.forward_snapshots WHERE id=EXCLUDED.snapshot_id)>
                  (SELECT exported_at FROM market.forward_snapshots WHERE id=market.current_forward_snapshot.snapshot_id)'''),{'id':snapshot})
    return dict(status='complete',snapshot_id=str(snapshot),rows=len(rows))


def build_forward(engine,as_of):
    future=as_of+timedelta(days=365)
    with engine.connect().execution_options(isolation_level='REPEATABLE READ') as conn,conn.begin():
        rows=[dict(r) for r in conn.execute(text('SELECT * FROM market.current_forward WHERE category_tag=ANY(:tags)'),{'tags':TAGS}).mappings()]
        contracts=[dict(r) for r in conn.execute(text('''SELECT cn_id,buyer,buyer_key,supplier,category_family,original_value,current_value,
            current_end_date AS date,source_url FROM market.current_analytical_contracts
            WHERE current_end_date>=:asof AND current_end_date<:future AND payload->>'category_tag'=ANY(:tags)
            ORDER BY current_end_date,cn_id'''),{'asof':as_of,'future':future,'tags':TAGS}).mappings()]
        snapshots=[dict(r) for r in conn.execute(text('SELECT s.* FROM market.forward_snapshots s JOIN market.current_forward_snapshot c ON c.snapshot_id=s.id')).mappings()]
    signals=[]
    for row in rows:
        plan=row['source']=='AusTender plan';day=row['expected_from'] if plan else row['closes_at'].date() if row['closes_at'] else None
        if plan:
            if row['expected_to'] and row['expected_to']<as_of:continue
            if row['expected_from'] and row['expected_from']>=future:continue
        elif not day or not as_of<=day<future or (row['status'] or '').casefold()!='open':continue
        signals.append(dict(row,date=day,lane='healthcare' if row['category_tag'] in ['healthcare_staffing','nursing_agency'] else 'state' if row['source'] in ['buy.nsw','Tenders ACT'] else 'federal',
                            signal='published_plan' if plan else 'open_notice_requires_live_recheck',why_now='Published planning window' if plan else 'Recorded closing date',target_roles=row['category_tag']))
    for row in contracts:
        signals.append(dict(row,source='AusTender contract',source_id=row['cn_id'],title='Contract with recorded end date',lane='federal',
                            signal='recorded_contract_end; retender_unconfirmed',why_now='Research future buying cycle; extensions remain possible',target_roles=row['category_family']))
    signals.sort(key=lambda r:(r['date'] or date.max,r['source'],r['source_id']))
    return dict(as_of=as_of,status='research; no retender or invitation inferred',signals=signals,snapshots=snapshots,
                limitations=['Recorded contract end dates are not announced re-tenders and values are whole contract values, not remaining revenue.',
                             'Plans describe expected windows and can change; undated plans remain visible without an invented month.',
                             'Open notices come from the retained snapshot and require a fresh official listing check before response effort.',
                             'Titles and categories drive draft tagging; no contact data or descriptions are imported.',
                             'Buyer labels remain separate; no unreviewed agency lineage is applied.'])


def write_forward(report,directory):
    return write_report_files(report,directory,'forward_pipeline',report['signals'],
        ['date','buyer','title','lane','signal','why_now','target_roles','source_url'],'Forward procurement research')
