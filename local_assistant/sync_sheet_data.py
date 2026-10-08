"""Read project-registered Sheets into a separate private, versioned SQLite store.

Only GET requests and the spreadsheets.readonly scope are used. No Sheet edits,
Drive discovery, publishing, or scraper-database writes are performed.
"""
import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import quote
import assistant

SCOPE='https://www.googleapis.com/auth/spreadsheets.readonly'
API='https://sheets.googleapis.com/v4/spreadsheets/'
SCHEMA='''
CREATE TABLE IF NOT EXISTS sheet_sources(
 source_key TEXT, spreadsheet_id TEXT, workbook_title TEXT, tab_id INTEGER,
 tab_title TEXT, columns_json TEXT, active_snapshot TEXT, row_count INTEGER,
 last_read_utc TEXT, status TEXT, error TEXT,
 PRIMARY KEY(source_key,tab_id));
CREATE TABLE IF NOT EXISTS sheet_snapshots(
 snapshot_id TEXT PRIMARY KEY, source_key TEXT, tab_id INTEGER, columns_json TEXT,
 read_started_utc TEXT, read_finished_utc TEXT, content_sha256 TEXT, row_count INTEGER);
CREATE TABLE IF NOT EXISTS sheet_rows(
 id INTEGER PRIMARY KEY, snapshot_id TEXT, row_number INTEGER, values_json TEXT,
 cells_json TEXT, row_text TEXT, UNIQUE(snapshot_id,row_number));
CREATE VIRTUAL TABLE IF NOT EXISTS sheet_fts USING fts5(row_text);
CREATE INDEX IF NOT EXISTS sheet_snapshot_rows ON sheet_rows(snapshot_id,row_number);
'''

def now():return datetime.now(timezone.utc).isoformat()

def column_letter(index):
    result='';index+=1
    while index:
        index,remainder=divmod(index-1,26);result=chr(65+remainder)+result
    return result

def columns(header,width):
    labels=[];seen=set()
    for index in range(width):
        label=str(header[index]).strip() if index<len(header) else ''
        label=label or 'column_'+column_letter(index)
        if label.casefold() in seen:label+=' ['+column_letter(index)+']'
        seen.add(label.casefold());labels.append(label)
    return labels

def _store_tab(connection,source_key,spreadsheet_id,workbook_title,tab_id,tab_title,values,started):
    """Switch the active snapshot only after a complete tab fetch and committed rows."""
    finished=now();width=max((len(row) for row in values),default=0)
    labels=columns(values[0] if values else [],width)
    encoded=json.dumps(values,ensure_ascii=False,separators=(',',':'))
    digest=hashlib.sha256(encoded.encode()).hexdigest()
    identity=f'{source_key}:{spreadsheet_id}:{tab_id}:{digest}'
    snapshot=hashlib.sha256(identity.encode()).hexdigest()
    existing=connection.execute('SELECT 1 FROM sheet_snapshots WHERE snapshot_id=?',(snapshot,)).fetchone()
    nonempty=[(number,row) for number,row in enumerate(values[1:],2) if any(str(cell).strip() for cell in row)]
    if not existing:
        connection.execute('INSERT INTO sheet_snapshots VALUES (?,?,?,?,?,?,?,?)',
            (snapshot,source_key,tab_id,json.dumps(labels),started,finished,digest,len(nonempty)))
        for number,cells in nonempty:
            mapped={label:str(cells[index]) if index<len(cells) else '' for index,label in enumerate(labels)}
            text='\n'.join(label+': '+value for label,value in mapped.items() if value)
            cursor=connection.execute('INSERT INTO sheet_rows(snapshot_id,row_number,values_json,cells_json,row_text) VALUES (?,?,?,?,?)',
                (snapshot,number,json.dumps(mapped,ensure_ascii=False),json.dumps(cells,ensure_ascii=False),text))
            connection.execute('INSERT INTO sheet_fts(rowid,row_text) VALUES (?,?)',(cursor.lastrowid,text))
    connection.execute('INSERT OR REPLACE INTO sheet_sources VALUES (?,?,?,?,?,?,?,?,?,?,?)',
        (source_key,spreadsheet_id,workbook_title,tab_id,tab_title,json.dumps(labels),snapshot,len(nonempty),finished,'synced',''))
    connection.commit()
    return len(nonempty)

def store_tab(connection,*args,**kwargs):
    with connection:
        return _store_tab(connection,*args,**kwargs)

def read_client():
    from google.oauth2.service_account import Credentials
    from google.auth.transport.requests import AuthorizedSession
    project=assistant.DB_DIR.resolve().parent
    sys.path.insert(0,str(project))
    import settings
    if not settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT:
        raise ValueError('Project Google Sheets service account is not configured')
    info=json.loads(settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT)
    return AuthorizedSession(Credentials.from_service_account_info(info,scopes=[SCOPE]))

def get_json(session,path,params=None):
    for attempt in range(5):
        response=session.get(API+path,params=params,timeout=45)
        if response.status_code in (429,500,502,503,504) and attempt<4:
            time.sleep(min(16,2**attempt));continue
        if response.status_code!=200:
            # Never return remote bodies that may contain project identifiers or cell data.
            raise ValueError(f'Google Sheets read failed (HTTP {response.status_code})')
        if len(response.content)>32_000_000:raise ValueError('Sheet response exceeds 32 MB; use smaller batches')
        return response.json()
    raise ValueError('Google Sheets retry limit exceeded')

def sync(only=None,reports=None):
    registry_path=assistant.DB_DIR/'sheets_registry.json'
    registry=json.loads(registry_path.read_text(encoding='utf-8'))
    if not isinstance(registry,dict):raise ValueError('Invalid Sheets registry')
    if only and any(key not in registry for key in only):raise ValueError('Unregistered source key')
    registry={key:value for key,value in registry.items() if not only or key in only}
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{20,160}',value) for value in registry.values()):
        raise ValueError('Invalid registered spreadsheet ID')
    path=assistant.DB_DIR/'sheet_imports.db'
    session=read_client();results=[]
    with closing(sqlite3.connect(path,timeout=15)) as connection:
        connection.executescript(SCHEMA);connection.commit()
        for source_key,spreadsheet_id in registry.items():
            try:
                metadata=get_json(session,spreadsheet_id,{'fields':'properties(title),sheets(properties(sheetId,title,gridProperties(rowCount,columnCount)))'})
                tabs=metadata.get('sheets',[]);current_ids=[]
                for tab in tabs:
                    properties=tab['properties'];tab_id=properties['sheetId'];title=properties['title'];current_ids.append(tab_id)
                    started=now();values=[];grid=properties['gridProperties']
                    try:
                        last_column=column_letter(grid['columnCount']-1)
                        total_rows=grid['rowCount']
                        for offset in range(1,total_rows+1,2000):
                            end=min(total_rows,offset+1999)
                            cell_range="'"+title.replace("'","''")+f"'!A{offset}:{last_column}{end}"
                            package=get_json(session,spreadsheet_id+'/values/'+quote(cell_range,safe=''),
                                             {'valueRenderOption':'FORMATTED_VALUE','majorDimension':'ROWS'})
                            part=package.get('values',[])
                            # Retain original row numbers across trailing-empty API ranges.
                            values.extend(part+[[]]*(end-offset+1-len(part)))
                        count=store_tab(connection,source_key,spreadsheet_id,metadata['properties']['title'],tab_id,title,values,started)
                        result={'source_key':source_key,'tab':title,'status':'synced','rows':count,'read_finished_utc':now()}
                    except Exception as exc:
                        connection.execute('UPDATE sheet_sources SET status=?,error=? WHERE source_key=? AND tab_id=?',
                            ('read_failed_previous_snapshot',type(exc).__name__,source_key,tab_id));connection.commit()
                        result={'source_key':source_key,'tab':title,'status':'failed','error_type':type(exc).__name__}
                    results.append(result);print(json.dumps(result),flush=True)
                if current_ids:
                    connection.execute('UPDATE sheet_sources SET status=? WHERE source_key=? AND tab_id NOT IN ('+','.join('?' for _ in current_ids)+')',
                        ['removed_from_workbook',source_key]+current_ids);connection.commit()
            except Exception as exc:
                connection.execute('UPDATE sheet_sources SET status=?,error=? WHERE source_key=?',
                    ('read_failed_previous_snapshot',type(exc).__name__,source_key));connection.commit()
                results.append({'source_key':source_key,'status':'failed','error_type':type(exc).__name__})
                print(json.dumps(results[-1]),flush=True)
    session.close()
    reports=Path(reports) if reports is not None else Path(__file__).parent/'reports';reports.mkdir(exist_ok=True)
    manifest={'completed_at_utc':now(),'database':str(path),'scope':SCOPE,'results':results,
              'limit':'Per-tab reads, not a transaction across Google Sheets. Formatted values; first row treated as headers. Failed reads preserve previous snapshots. No writes to Sheets.'}
    temporary=reports/'sheets-import.json.partial'
    temporary.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    temporary.replace(reports/'sheets-import.json')
    return not any(row['status']=='failed' for row in results)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--only',nargs='+')
    args=parser.parse_args()
    try:raise SystemExit(0 if sync(args.only) else 1)
    except (ValueError,FileNotFoundError) as exc:raise SystemExit(str(exc))
