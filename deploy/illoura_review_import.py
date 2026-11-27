#!/usr/bin/env python3
"""Root boundary for queued reviews; retry lock contention and Sydney blackout."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
from zoneinfo import ZoneInfo

CONFIG = Path('/etc/illoura-market/research-refresh.json')
FALLBACK_CONFIG = Path('/etc/illoura-market/refresh.json')
LOCK = Path('/var/lib/illoura-market-refresh/refresh.lock')
INBOX = '/var/lib/illoura-review/inbox'
LEDGER = '/var/lib/illoura-review/ledger'
HARD_LIMIT = 120


def blackout(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('Australia/Sydney'))
    # Do not begin a bounded two-minute job that could overlap 05:30.
    return 328 <= local.hour * 60 + local.minute < 570


def configuration(path):
    st = path.stat()
    if path.is_symlink() or not path.is_file() or st.st_uid != 0 or st.st_mode & 0o077:
        raise ValueError('Configuration must be root-owned mode 0600')
    data = json.loads(path.read_text(encoding='utf-8'))
    if set(data) != {'data_root', 'build', 'tools'}:
        raise ValueError('Unexpected configuration fields')
    root = Path(data['data_root']).resolve(strict=True)
    build = Path(data['build']).resolve(strict=True)
    if root != Path('/home/ubuntu/illoura-market-data') or not build.is_relative_to(root / 'builds'):
        raise ValueError('Unexpected research owner path')
    return root, build


def run_application(root, build, timeout=HARD_LIMIT):
    argv = ['runuser', '-u', 'ubuntu', '--', str(root / 'venv/bin/python'),
            '-m', 'market_intelligence.review_inbox', '--environment', str(root / 'database.env'),
            '--inbox', INBOX, '--ledger', LEDGER]
    process = subprocess.Popen(argv, cwd=build, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        process.wait(timeout=timeout - 5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=5)
        return {'status': 'failed', 'reason': 'timeout'}
    return {'status': 'complete' if process.returncode == 0 else 'failed'}


def config_path():
    # The service runs as root; an unprivileged caller (e.g. staged tests) cannot stat /etc/illoura-market.
    try:
        return CONFIG if CONFIG.exists() else FALLBACK_CONFIG
    except PermissionError:
        return CONFIG


def run(config=None, now=None):
    import fcntl
    if blackout(now):
        return {'status': 'deferred', 'reason': 'sydney_brief_blackout'}
    root, build = configuration(config or config_path())
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'deferred', 'reason': 'shared_research_lock_busy'}
        if blackout(now):
            return {'status': 'deferred', 'reason': 'sydney_brief_blackout'}
        return run_application(root, build)


def main(argv=None):
    argparse.ArgumentParser(description=__doc__).parse_args(argv)
    os.umask(0o027)
    try:
        result = run()
    except Exception as exc:
        result = {'status': 'failed', 'reason': type(exc).__name__}
    print(json.dumps(result))
    return 0 if result['status'] == 'complete' else 75


if __name__ == '__main__':
    raise SystemExit(main())
