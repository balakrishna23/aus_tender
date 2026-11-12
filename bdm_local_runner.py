"""Run and supervise the local BuyICT monitor; usable by Task Scheduler or --loop."""
import argparse
import html
import json
import os
import subprocess
import sys
import time
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import bdm_monitor as monitor
from project_paths import PROJECT_DIR, database_directory


def atomic_text(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(text,encoding='utf-8'); os.replace(temp,path)


def record_local_messages(db,folder,cfg):
    """Keep all observed event revisions, including superseded ones, without sending."""
    folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'messages'; archive.mkdir(exist_ok=True)
    events=[dict(r) for r in db.execute('SELECT * FROM events ORDER BY created_at,event_id')]
    sections=['ILLOURA BUYICT LOCAL MESSAGE LOG',
              'Local records only. No email is sent by this journal.',
              'Updated: '+datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %B %Y %I:%M %p IST'),
              'Messages recorded: '+str(len(events)),
              'All change revisions are retained; superseded records are labelled.']
    for event in events:
        message=monitor.make_message([event],cfg,'<local-record@illouraservices.com>')
        body=message.get_body(preferencelist=('plain',)).get_content()
        header='\n'+'='*70+'\nEvent: '+event['event_id']+'\nQueue status: '+event['status']+'\nDetected UTC: '+event['created_at']+'\nLOCAL RECORD — EMAIL NOT SENT\n'
        sections.append(header+body)
        if not (archive/(event['event_id']+'.json')).exists():
            monitor.atomic_json(archive/(event['event_id']+'.json'),dict(event,local_record_only=True,email_sent=False))
            atomic_text(archive/(event['event_id']+'.txt'),header+body)
    if not events:
        sections.append('\nNo new or changed requirements have been detected since the monitoring baseline. Existing listings remain in the results tracker.')
    atomic_text(folder/'queued_messages.txt','\n\n'.join(sections)+'\n')
    monitor.atomic_json(folder/'queued_messages.json',{'local_only':True,'recorded':len(events),'events':events})
    return len(events)


def write_status(out, state, cfg, cycle):
    with closing(monitor.connect(state)) as db:
        meta=dict(db.execute('SELECT key,value FROM meta'))
        counts=dict(db.execute('SELECT status,count(*) FROM events GROUP BY status'))
        events=db.execute("SELECT kind,payload,created_at FROM events WHERE status='pending' ORDER BY created_at DESC LIMIT 20").fetchall()
        journal=Path(cfg.get('local_journal_dir') or out)
        recorded=record_local_messages(db,journal,cfg)
    last=meta.get('last_live_refresh_at')
    age=(datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds()/60 if last else None
    result=dict(cycle,source_last_success_utc=last,source_age_minutes=round(age,1) if age is not None else None,
                events=counts,email_enabled=cfg['send_enabled'],
                desktop_enabled=cfg.get('desktop_enabled',False),
                email_status='Enabled; inspect delivery receipts' if cfg['send_enabled'] else 'Local recording enabled; no automatic email',
                local_message_file=str(journal/'queued_messages.txt'),recorded_messages=recorded,
                updated_at_utc=datetime.now(timezone.utc).isoformat())
    monitor.atomic_json(out/'local_status.json',result)
    esc=html.escape
    rows=[]
    for e in events:
        v=json.loads(e['payload'])
        rows.append('<tr><td>'+esc(e['kind'].replace('_',' '))+'</td><td>'+esc(v.get('rfq_id') or '')+'</td><td>'+esc(v.get('title') or '')+'</td><td>'+esc(v.get('buyer') or '')+'</td></tr>')
    stale=age is None or age>45
    badge='Checking BuyICT now' if cycle['status']=='running' else ('Waiting for source runner; retry scheduled' if cycle['status']=='deferred' and not stale else ('Needs attention' if stale or cycle['status']!='ok' else 'Monitoring successfully'))
    body='''<!doctype html><html><head><meta charset="utf-8"><meta http-equiv="refresh" content="30"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Illoura BuyICT monitor</title><style>body{font:16px Arial;line-height:1.5;max-width:1100px;margin:32px auto;padding:0 20px;color:#26332f}h1{color:#183c38}section{background:#edf2ed;padding:18px;margin:18px 0}table{border-collapse:collapse;width:100%}th,td{text-align:left;padding:10px;border-bottom:1px solid #ccd8ce}a{color:#075985}</style></head><body><h1>Illoura BuyICT monitor</h1>'''
    body+='<section><strong id="monitor-status">'+badge+'</strong><p>Checks every 15 minutes while this machine is awake, connected and the configured user is logged in.</p>'
    display_time=datetime.fromisoformat(last).astimezone(ZoneInfo('Asia/Kolkata')).strftime('%d %B %Y, %I:%M %p IST') if last else 'None recorded'
    body+='<p>Last successful source check: '+esc(display_time)+'</p>'
    body+='<p>'+esc(result['email_status'])+'</p><p>Queued changes: '+str(counts.get('pending',0))+'</p></section>'
    body+='<p><a href="http://127.0.0.1:8767">Open editable BDM working view</a> | <a href="results.csv">Current results CSV</a> | <a href="alert_preview.html">Queued alert preview</a> | <a href="local_status.json">Detailed status</a></p>'
    body+='<p><a href="'+esc((journal/'queued_messages.txt').resolve().as_uri(),quote=True)+'">All recorded messages</a></p>'
    body+='<h2>Latest queued changes</h2><table><tr><th>Change</th><th>ID</th><th>Requirement</th><th>Buyer</th></tr>'+(''.join(rows) or '<tr><td colspan="4">No new changes are waiting.</td></tr>')+'</table>'
    body+='<p>Public BuyICT only. Seller invitation, full response access and candidate fit require checking. This page refreshes every 30 seconds.</p>'
    body+='<script>const lastSuccess='+json.dumps(last)+';if(!lastSuccess || Date.now()-Date.parse(lastSuccess)>45*60000){document.getElementById("monitor-status").textContent="Needs attention: no successful source check within 45 minutes";}</script></body></html>'
    temp=out/('index.'+uuid.uuid4().hex+'.tmp'); temp.write_text(body,encoding='utf-8'); os.replace(temp,out/'index.html')
    return result


def run_cycle(config_path, out, state):
    cfg=monitor.config(config_path); started=datetime.now(timezone.utc)
    if cfg.get('workbench_enabled'):
        ensure_workbench(out,state)
    base=[sys.executable,'-u',str(PROJECT_DIR/'bdm_monitor.py'),'--config',str(config_path),
          '--state',str(state),'--out',str(out)]
    logs=out/'local_runs'; logs.mkdir(parents=True,exist_ok=True)
    log=logs/(started.strftime('%Y%m%dT%H%M%SZ')+'.log')
    cycle={'status':'running','started_at_utc':started.isoformat(),'log':str(log),'interval_minutes':15}
    write_status(out,state,cfg,cycle)
    try:
        with log.open('w',encoding='utf-8') as stream:
            command=base+['poll','--refresh']+(['--send'] if cfg['send_enabled'] else [])
            for attempt in range(cfg['busy_retries'] + 1):
                result=subprocess.run(command,cwd=PROJECT_DIR,stdout=stream,stderr=subprocess.STDOUT,timeout=1400)
                if result.returncode != 75 or attempt == cfg['busy_retries']:
                    break
                stream.write('Source busy; bounded retry pending.\n'); stream.flush()
                time.sleep(cfg['busy_retry_seconds'])
            cycle['poll_exit_code']=result.returncode
            if result.returncode==0:
                preview=subprocess.run(base+['preview'],cwd=PROJECT_DIR,stdout=stream,stderr=subprocess.STDOUT,timeout=60)
                cycle['preview_exit_code']=preview.returncode
                cycle['status']='ok' if preview.returncode==0 else 'preview_failed'
            elif result.returncode==75:
                cycle['status']='deferred'
            else:
                cycle['status']='poll_failed'
    except Exception as exc:
        cycle.update(status='runner_failed',error_type=type(exc).__name__)
    cycle['completed_at_utc']=datetime.now(timezone.utc).isoformat()
    result=write_status(out,state,cfg,cycle)
    from bdm_workflow import operational_alert
    with closing(monitor.connect(state)) as db:
        age = result['source_age_minutes']
        if cycle['status'] not in ('ok','deferred') or age is None or age > cfg['max_source_age_minutes']:
            operational_alert(db,cfg,'monitor_health','No successful recent BuyICT check. Open the status page for details.',datetime.now(timezone.utc))
    print(json.dumps(result,indent=2),flush=True)
    return 0 if cycle['status'] in ('ok','deferred') else 1


def ensure_workbench(out,state):
    from urllib.request import urlopen
    try:
        with urlopen('http://127.0.0.1:8767/api/identity',timeout=2) as response:
            if json.loads(response.read()).get('service')=='illoura-bdm':
                return
    except Exception:
        pass
    log=out/'workbench_service.log'
    with log.open('a',encoding='utf-8') as stream:
        subprocess.Popen([sys.executable,str(PROJECT_DIR/'bdm_workbench.py'),'--state',str(state),'--out',str(out)],
                         cwd=PROJECT_DIR,stdout=stream,stderr=stream,
                         creationflags=0x08000000 if os.name=='nt' else 0)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,default=PROJECT_DIR/'inputs/bdm/monitor.yaml')
    parser.add_argument('--out',type=Path,default=PROJECT_DIR/'exports/bdm')
    parser.add_argument('--state',type=Path,default=database_directory()/'bdm_monitor.db')
    parser.add_argument('--loop',action='store_true')
    args=parser.parse_args(); args.out.mkdir(parents=True,exist_ok=True)
    with monitor.lock(args.out/'.local_runner.lock'):
        while True:
            start=time.monotonic(); code=run_cycle(args.config.resolve(),args.out.resolve(),args.state.resolve())
            if not args.loop:
                return code
            time.sleep(max(1,15*60-(time.monotonic()-start)))


if __name__=='__main__':
    raise SystemExit(main())
