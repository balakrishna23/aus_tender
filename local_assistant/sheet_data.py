"""Bounded read-only answers from imported Sheet rows, with row-level provenance."""
import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime,timezone
from urllib.parse import quote

def fts_term(value):return '"'+value.replace('"','""')+'"'

def _query_sheets(question,plan,query,db_dir,topic='',contract_ids=None):
    sections=[];facts=[];missing=[]
    if not (db_dir/'sheet_imports.db').is_file():
        return {'sections':['Google Sheets has not been imported yet.'],'facts':[],
                'missing':['Run sync_sheet_data.py to create a read-only Sheet snapshot.']}
    def q(sql,params=()):return query('sheet_imports.db',sql,params)
    source=re.search(r'\b(?:workbook|dataset|source)\s+["“]?([a-z_]+)',question,re.I)
    tab=re.search(r'\b(?:tab|worksheet)\s+["“]([^"”]+)["”]',question,re.I)
    if not tab:tab=re.search(r'\b(?:tab|worksheet)\s+([a-z_][\w]*)',question,re.I)
    tab_name=tab.group(1) if tab else 'Tracker' if re.search(r'\bTracker\b',question,re.I) else None
    row=re.search(r'\brow\s+(\d+)\b',question,re.I)
    phrase=re.search(r'\b(?:containing|mentions?|search for)\s+["“]([^"”]+)["”]',question,re.I)
    clauses=["s.status!='removed_from_workbook'"];params=[]
    if source:clauses.append('s.source_key=?');params.append(source.group(1).lower())
    if tab_name:clauses.append('s.tab_title=? COLLATE NOCASE');params.append(tab_name)
    predicate=' AND '.join(clauses)
    sources=q('SELECT s.source_key,s.workbook_title,s.tab_id,s.tab_title,s.row_count,s.last_read_utc,s.status '
              'FROM sheet_sources s WHERE '+predicate+' ORDER BY s.source_key,s.tab_title',params)
    if not sources:
        return {'sections':['No imported workbook/tab matches that scope.'],'facts':[],
                'missing':['Check the registered source key and exact tab title. No broader Sheet search was substituted.']}
    terms=contract_ids if contract_ids is not None else list(plan.get('contract_ids',[]))+list(plan.get('abns',[]))+list(plan.get('entities',[]))
    if contract_ids is None:
        if phrase:terms.append(phrase.group(1))
        if topic:terms.append(topic)
        elif plan.get('roles'):
            # Roles here are literal row search, not semantic claims about cell values.
            terms+=plan['roles']
        for person in ['Lane','Sirish']:
            if re.search(r'\b'+person+r'\b',question,re.I):terms.append(person)
    terms=list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))
    overview=not terms and not row and not re.search(r'\b(notes?|rows?|records?)\b',question,re.I)
    if overview:
        sections.append(f'Google Sheets snapshot: {len(sources)} imported tabs; {sum(r["row_count"] for r in sources):,} nonempty data rows in this scope. First row is treated as headers.')
        facts=[{**r,'view':'google_sheets_coverage'} for r in sources[:16]]
    else:
        if row:clauses.append('r.row_number=?');params.append(int(row.group(1)))
        if re.search(r'\bnotes?\b',question,re.I):
            clauses.append("EXISTS (SELECT 1 FROM json_each(r.values_json) j WHERE lower(j.key) LIKE '%note%' AND trim(CAST(j.value AS TEXT))!='')")
        if terms:
            # CN-linked context uses OR across selected contract IDs; ordinary filters use AND.
            operator=' OR ' if contract_ids is not None else ' AND '
            match=operator.join(fts_term(term) for term in terms)
            clauses.append('r.id IN (SELECT rowid FROM sheet_fts WHERE sheet_fts MATCH ?)');params.append(match)
        where=' AND '.join(clauses)
        join=' FROM sheet_rows r JOIN sheet_sources s ON s.active_snapshot=r.snapshot_id WHERE '+where
        total=q('SELECT COUNT(*) AS n'+join,params)[0]['n']
        rows=q('SELECT s.source_key,s.spreadsheet_id,s.workbook_title,s.tab_id,s.tab_title,s.last_read_utc,s.status,'
               'r.row_number,r.values_json'+join+' ORDER BY s.source_key,s.tab_title,r.row_number LIMIT 8',params)
        sections.append(f'Google Sheets: {total:,} matching imported rows; showing {len(rows)}. '
                        'These are Sheet contents at the displayed sync time, not a live read or independently verified claims.')
        for record in rows:
            fields=json.loads(record.pop('values_json'))
            if re.search(r'\bnotes?\b',question,re.I):
                # Display a focussed excerpt while preserving the complete row as evidence.
                excerpt={key:value for key,value in fields.items() if re.search(r'note|owner|status|stage|organisation',key,re.I) and value}
                if not excerpt:excerpt={key:value for key,value in fields.items() if value}
            else:excerpt={key:value for key,value in fields.items() if value}
            record['fields']=fields;record['view']='google_sheets_row'
            record['source_url']='https://docs.google.com/spreadsheets/d/'+record.pop('spreadsheet_id')+'/edit#gid='+str(record['tab_id'])+'&range=A'+str(record['row_number'])
            record['proof_scope']='Imported, formatted Sheet cells; user-entered notes and statuses are not independently verified.'
            record['excerpt']='; '.join(key+': '+value for key,value in list(excerpt.items())[:6])[:1100]
            facts.append(record)
    for source_record in sources:
        if source_record['status']!='synced':missing.append('One or more selected Sheet reads failed; previous snapshots may be shown.')
        checked=datetime.fromisoformat(source_record['last_read_utc'])
        if (datetime.now(timezone.utc)-checked).total_seconds()>3600:
            missing.append('The selected Sheets snapshot is older than one hour. Sync before relying on recent edits.')
    missing.append('Sheet edits after the displayed sync time are not visible. Sync is manual; no Sheet data is changed by the assistant.')
    if any(key in plan for key in ['min_value','max_value']) or plan.get('explicit_window') or plan.get('exclude'):
        missing.append('Sheet rows are keyword/row lookups. Contract date, amount and exclusion calculations come from the procurement databases; generic Sheet cells are not normalized for those filters.')
    if re.search(r'\b(sum|total value|average|median|profit|margin)\b',question,re.I):
        missing.append('Arbitrary Sheet financial calculations are not implemented; displayed rows do not establish a sum or average.')
    if re.search(r'\b(add|added|edit|edited|changed|who wrote|when written)\b',question,re.I):
        missing.append('These are cell snapshots, not Google edit history. An owner name does not establish who edited a cell or when.')
    return {'sections':sections,'facts':facts,'missing':list(dict.fromkeys(missing))}

def query_sheets(question,plan,query,db_dir,topic='',contract_ids=None):
    path=db_dir/'sheet_imports.db'
    if not path.is_file():return _query_sheets(question,plan,query,db_dir,topic,contract_ids)
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as connection:
        connection.row_factory=sqlite3.Row;connection.execute('PRAGMA query_only=ON');connection.execute('BEGIN')
        deadline=time.monotonic()+8
        connection.set_progress_handler(lambda:int(time.monotonic()>deadline),1000)
        def read(database,sql,params=()):
            return [dict(row) for row in connection.execute(sql,params)]
        return _query_sheets(question,plan,read,db_dir,topic,contract_ids)
