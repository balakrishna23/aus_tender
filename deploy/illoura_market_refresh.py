"""Optional bounded research refresh within the existing daily brief. No send or new timer."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import uuid
from zoneinfo import ZoneInfo

CONFIG=Path('/etc/illoura-market/refresh.json')
STATE=Path('/var/lib/illoura-market-refresh')
PACKAGE=Path('/opt/illoura-routine/market_research.json')
COLLECTORS=Path('/opt/aus_tender/databases')
PROJECT_PYTHON=Path('/opt/aus_tender/.venv/bin/python')


def command(argv,timeout,cwd=None,env=None):
    process=subprocess.Popen([str(a) for a in argv],cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                             text=True,start_new_session=True)
    try:out,err=process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid,signal.SIGKILL);process.communicate()
        raise
    if process.returncode:raise RuntimeError('Research step failed')
    return out


def publish_package(source,data_root,destination=PACKAGE,state=STATE):
    """Validate before an atomic root-owned publication. Retain prior packages by checksum."""
    import sys
    import grp
    import pwd
    sys.path.insert(0,'/opt/illoura-routine/scripts')
    from market_research_context import read_market_context
    source=Path(source).resolve(strict=True);data_root=Path(data_root).resolve(strict=True)
    if not source.is_relative_to(data_root) or source.name!='market_brief.json' or not source.is_file():
        raise ValueError('Package escaped private research storage')
    if source.stat().st_uid!=pwd.getpwnam('ubuntu').pw_uid:raise ValueError('Unexpected package owner')
    now=datetime.now(timezone.utc);edition=now.astimezone(ZoneInfo('Asia/Kolkata')).date()
    _,validation=read_market_context(source,edition,now)
    if validation['status']!='included':raise ValueError('Package validation failed')
    data=source.read_bytes();history=state/'history';history.mkdir(mode=0o700,parents=True,exist_ok=True)
    if destination.exists():
        previous=destination.read_bytes();old=history/(hashlib.sha256(previous).hexdigest()+'.json')
        if not old.exists():old.write_bytes(previous)
    # A reader sees either the old complete file or the new complete file.
    descriptor,temporary=tempfile.mkstemp(prefix='.market-',dir=destination.parent)
    with os.fdopen(descriptor,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    os.chown(temporary,0,grp.getgrnam('scraper').gr_gid);os.chmod(temporary,0o640)
    os.replace(temporary,destination)
    return {'sha256':hashlib.sha256(data).hexdigest(),'as_of':validation['as_of']}


def refresh(config=CONFIG,timeout=210):
    if not config.is_file():return {'status':'disabled','reason':'research refresh not configured'}
    st=config.stat()
    if st.st_uid!=0 or st.st_mode&0o022:raise ValueError('Refresh configuration must be root-owned and not writable by other users')
    data=json.loads(config.read_text())
    if set(data)!={'data_root','build','tools'}:raise ValueError('Unexpected refresh configuration fields')
    root=Path(data['data_root']).resolve(strict=True);build=Path(data['build']).resolve(strict=True);tools=Path(data['tools']).resolve(strict=True)
    if not build.is_relative_to(root/'builds'):raise ValueError('Unexpected research build path')
    if tools.stat().st_uid!=0 or tools.stat().st_mode&0o022:raise ValueError('Export tools must be protected')
    import pwd
    run=uuid.uuid4().hex;stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+run[:8]
    temporary=Path('/var/tmp')/('illoura-market-exports-'+stamp)
    temporary.mkdir(mode=0o700)
    scraper=pwd.getpwnam('scraper');ubuntu=pwd.getpwnam('ubuntu')
    os.chown(temporary,scraper.pw_uid,scraper.pw_gid)
    started=datetime.now(timezone.utc)
    def remaining():
        value=timeout-(datetime.now(timezone.utc)-started).total_seconds()
        if value<=0:raise TimeoutError('Research refresh deadline reached')
        return value
    for script,args in [('extract_procurement.py',['--directory',str(COLLECTORS)]),('extract_forward.py',['--source',str(COLLECTORS/'intelligence.db')])]:
        destination=temporary/('procurement.db' if script=='extract_procurement.py' else 'forward.db')
        command(['runuser','-u','scraper','--',PROJECT_PYTHON,tools/'market_intelligence'/script,*args,'--destination',destination],remaining())
    exports=root/'exports'/('automatic-'+stamp);exports.mkdir(mode=0o700)
    os.chown(exports,ubuntu.pw_uid,ubuntu.pw_gid)
    for name in ['procurement.db','forward.db']:
        target=exports/name;shutil.copyfile(temporary/name,target);os.chmod(target,0o600);os.chown(target,ubuntu.pw_uid,ubuntu.pw_gid)
    output=root/'reports/automatic'/stamp
    # The application interpreter and database environment are used only as their unprivileged owner.
    edition=datetime.now(timezone.utc).astimezone(ZoneInfo('Asia/Kolkata')).date()
    result=json.loads(command(['runuser','-u','ubuntu','--',root/'venv/bin/python','-m','market_intelligence.refresh',
        '--environment',root/'database.env','--procurement-export',exports/'procurement.db','--forward-export',exports/'forward.db',
        '--output',output,'--as-of',str(edition)],remaining(),cwd=build))
    if result['status']!='complete':raise RuntimeError('Application research refresh failed')
    publication=publish_package(result['package'],root)
    return dict(status='complete',run_id=run,publication=publication,source_refresh='current collector snapshots; other publishers retain their recorded release dates')


def main(argv=None):
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--timeout',type=int,default=210);args=p.parse_args(argv)
    # The brief passes at most 900 s and also bounds it by the 08:15 send (illoura_daily_brief.py).
    if not 1<=args.timeout<=900:raise ValueError('Refresh timeout must be within the morning budget')
    lock=None
    try:
        if CONFIG.exists():
            import fcntl
            STATE.mkdir(mode=0o700,parents=True,exist_ok=True)
            lock=(STATE/'refresh.lock').open('a')
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Pass CONFIG explicitly: refresh()'s default is bound at definition time.
        result=refresh(CONFIG,timeout=args.timeout)
    except BlockingIOError:result={'status':'busy','reason':'another research refresh owns the lock'}
    except Exception as exc:result={'status':'failed','error_code':type(exc).__name__}
    finally:
        if lock is not None:lock.close()
    if CONFIG.exists():
        STATE.mkdir(mode=0o700,parents=True,exist_ok=True);(STATE/'status.json').write_text(json.dumps(dict(result,checked_at=datetime.now(timezone.utc).isoformat()),indent=2))
    print(json.dumps(result));return 0 if result['status'] in ['complete','disabled'] else 1


if __name__=='__main__':raise SystemExit(main())
