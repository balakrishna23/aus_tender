"""Refresh collected procurement evidence and reports; no mail, schedules or privileged writes."""
from datetime import date,datetime,timezone
import json
import os
from pathlib import Path
import uuid
import time

from sqlalchemy import text

from .db import engine
from .procurement import import_procurement
from .matching import bridge_buyers
from .forward import import_forward,build_forward,write_forward
from .analytics import build_report,write_report
from .brief import build_brief,write_brief
from .readiness import readiness
from .reporting import json_value
from .crm import build_crm,export_crm


def refresh(db,procurement_export,forward_export,directory,as_of):
    """Keep an immutable run. The caller publishes the package only after every step succeeds."""
    directory=Path(directory).resolve()
    directory.mkdir(parents=True,exist_ok=False)
    run_id=uuid.uuid4()
    report=dict(run_id=str(run_id),as_of=as_of,status='running',steps=[],started_at=datetime.now(timezone.utc))
    with db.begin() as conn:
        conn.execute(text("INSERT INTO market.pipeline_runs(id,source,status) VALUES(:id,'market_refresh','running')"),{'id':run_id})

    def save():
        (directory/'refresh_report.json').write_text(json.dumps(report,default=json_value,indent=2),encoding='utf-8')

    def step(name,action):
        item={'name':name,'status':'running','started_at':datetime.now(timezone.utc)}
        report['steps'].append(item)
        save()
        started=time.monotonic()
        try:
            result=action()
            item.update(status='complete',result=result)
        except Exception as exc:
            report['failed_step']=name
            item.update(status='failed',error_code=type(exc).__name__)
            raise
        finally:
            item['seconds']=round(time.monotonic()-started,3)
            save()
        return result

    try:
        # Imports retain their own source histories. Buyer matching never records human approvals.
        step('procurement',lambda:import_procurement(db,procurement_export))
        step('buyer_proposals',lambda:bridge_buyers(db,procurement_export))
        step('forward',lambda:import_forward(db,forward_export))
        federal={}
        def buyer_report():
            federal.update(build_report(db,as_of))
            return write_report(federal,directory/'buyers')
        step('buyer_report',buyer_report)
        step('forward_report',lambda:write_forward(build_forward(db,as_of),directory/'forward'))
        step('crm_export',lambda:export_crm(build_crm(db,as_of,federal=federal),directory/'crm'))
        ready=step('readiness',lambda:readiness(db,as_of))
        (directory/'readiness.json').write_text(json.dumps(ready,default=json_value,indent=2),encoding='utf-8')
        # This is the last output: failed imports/reports never produce a publishable result.
        step('market_package',lambda:write_brief(build_brief(db,as_of,federal=federal),directory/'digest'))
        report.update(status='complete',package=str(directory/'digest/market_brief.json'))
    except Exception as exc:
        report.update(status='failed',error_code=type(exc).__name__)
    finally:
        report['finished_at']=datetime.now(timezone.utc)
        save()
        with db.begin() as conn:
            conn.execute(text('''UPDATE market.pipeline_runs SET status=:status,finished_at=now(),error_code=:error,
                metrics=CAST(:metrics AS jsonb) WHERE id=:id'''),
                {'id':run_id,'status':report['status'],'error':report.get('error_code'),
                 'metrics':json.dumps({'steps_completed':sum(s['status']=='complete' for s in report['steps']),'report_directory':str(directory)})})
    return report


def main(argv=None):
    import argparse
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--environment',required=True,type=Path)
    p.add_argument('--procurement-export',required=True)
    p.add_argument('--forward-export',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--as-of',required=True,type=date.fromisoformat)
    args=p.parse_args(argv)
    # Called as the unprivileged database owner; the private URL is never placed in argv or stdout.
    for line in args.environment.read_text(encoding='utf-8').splitlines():
        if '=' in line and not line.lstrip().startswith('#'):
            key,value=line.split('=',1)
            if key=='ILLOURA_MARKET_DATABASE_URL':os.environ[key]=value
    db=engine()
    try:
        result=refresh(db,args.procurement_export,args.forward_export,args.output,args.as_of)
        print(json.dumps({k:result[k] for k in ['run_id','status','package','error_code'] if k in result}))
        return 0 if result['status']=='complete' else 1
    finally:db.dispose()


if __name__=='__main__':raise SystemExit(main())
