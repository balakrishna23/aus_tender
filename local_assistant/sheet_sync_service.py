"""Single read-only Sheets import job, launched only by an explicit UI click."""
import json
import threading
from datetime import datetime,timezone
from pathlib import Path

LOCK=threading.Lock()
STATE={'status':'idle'}

def state():
    with LOCK:result=dict(STATE)
    path=Path(__file__).parent/'reports'/'sheets-import.json'
    if path.is_file() and result['status']!='running':
        report=json.loads(path.read_text(encoding='utf-8'))
        result['last_completed_at_utc']=report['completed_at_utc']
        result['tabs']=len(report['results'])
        result['rows']=sum(r.get('rows',0) for r in report['results'])
        result['failed_reads']=sum(r['status']=='failed' for r in report['results'])
    return result

def run():
    try:
        from sync_sheet_data import sync
        succeeded=sync()
        update={'status':'completed' if succeeded else 'partial','finished_at_utc':datetime.now(timezone.utc).isoformat()}
    except Exception as exc:
        update={'status':'failed','error_type':type(exc).__name__}
    with LOCK:STATE.update(update)

def start():
    with LOCK:
        if STATE['status']=='running':return dict(STATE)
        STATE.clear();STATE.update(status='running',started_at_utc=datetime.now(timezone.utc).isoformat())
        threading.Thread(target=run,daemon=True).start()
        return dict(STATE)
