"""Read-only operational checks. Never sends mail; failures are safe to poll externally."""
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
from illoura_cloud_ops import atomic_json,utc

def check(work=Path('/opt/illoura-routine'),served=Path('/srv/illoura-data'),token=Path('/etc/illoura-routine/gmail-token.json')):
    now=datetime.now(timezone.utc);checks={}
    def freshness(name,path,field):
        try:
            value=json.loads(path.read_text());stamp=value[field]
            age=(now-datetime.fromisoformat(stamp)).total_seconds()
            checks[name]={'status':'ok' if 0<=age<=26*3600 else 'stale','age_seconds':round(age),'at_utc':stamp}
        except (OSError,ValueError,KeyError,TypeError):checks[name]={'status':'unavailable'}
    freshness('publication',served/'current/manifest.json','snapshot_finished_utc')
    try:
        state=json.loads((work/'latest_run.json').read_text())
        run=Path(state['run_dir'])
        if run.parent.resolve()!=(work/'reports').resolve():raise ValueError('Unexpected run path')
        freshness('collection',run/'newsletter_evidence.json','generated_at_utc')
        checks['last_run']={'status':'ok' if state.get('status') in ['ready_for_editorial_review','reviewed_blocked_mailbox_authorization','sent'] else 'attention','phase':state.get('status')}
    except (OSError,ValueError,KeyError):checks['last_run']={'status':'unavailable'}
    coordinator=work/'coordinator_status.json'
    if coordinator.exists():
        try:
            value=json.loads(coordinator.read_text())
            elapsed=(now-datetime.fromisoformat(value['started_at_utc'])).total_seconds()
            healthy=value.get('status')=='completed' or (value.get('status')=='running' and 0<=elapsed<5*3600)
            checks['coordinator']={'status':'ok' if healthy else 'attention','phase':value.get('status')}
        except (OSError,ValueError,KeyError,TypeError):checks['coordinator']={'status':'unavailable'}
    disk=shutil.disk_usage(served)
    checks['disk']={'status':'ok' if disk.free>10*1024**3 and disk.free/disk.total>.15 else 'low','free_bytes':disk.free,'total_bytes':disk.total}
    retention = served / 'retention.json'
    if retention.exists():
        try:
            value = json.loads(retention.read_text(encoding='utf-8'))
            checks['retention'] = {
                'status': 'ok' if value.get('status') == 'ok' else 'attention',
                'checked_at_utc': value.get('checked_at_utc'),
                'reclaimed_bytes': value.get('reclaimed_bytes', 0),
                'observed_free_bytes_change': value.get('observed_free_bytes_change'),
                'retained_generation_bytes': value.get('retained_generation_bytes'),
                'retained_backup_bytes': value.get('retained_backup_bytes'),
                'deleted_directories': len(value.get('deleted', [])),
                'errors': len(value.get('errors', [])),
            }
        except (OSError, ValueError, KeyError, TypeError):
            checks['retention'] = {'status': 'unavailable'}
    # The expected sending mailbox is private server configuration, never source code.
    expected=os.environ.get('ILLOURA_GMAIL_SENDER','').strip().lower()
    if not expected:
        checks['mail_authorization']={'status':'unconfigured','detail':'Set ILLOURA_GMAIL_SENDER in /etc/illoura-ops/health.env'}
    else:
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.transport.requests import AuthorizedSession,Request
            creds=Credentials.from_authorized_user_file(str(token))
            if not creds.valid:creds.refresh(Request())
            session=AuthorizedSession(creds)
            response=session.get('https://gmail.googleapis.com/gmail/v1/users/me/profile',timeout=30)
            response.raise_for_status()
            checks['mail_authorization']={'status':'ok' if response.json()['emailAddress'].lower()==expected else 'identity_mismatch'}
            session.close()
        except Exception as error:
            checks['mail_authorization']={'status':'failed','error_type':type(error).__name__}
    ok=all(v['status']=='ok' for v in checks.values())
    return {'status':'ok' if ok else 'attention','checked_at_utc':utc(),'checks':checks,'email_sent':False,
            'monitoring_note':'Poll independently of this instance and Gmail to detect silence. A health file alone is not an external alert.'}

if __name__=='__main__':
    result=check()
    path=Path('/var/lib/illoura-health/status.json')
    atomic_json(path,result)
    shutil.chown(path,group='illoura-api');path.chmod(0o640)
    print(json.dumps(result,indent=2))
    raise SystemExit(0 if result['status']=='ok' else 1)
