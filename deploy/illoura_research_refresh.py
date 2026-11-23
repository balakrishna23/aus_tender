#!/usr/bin/env python3
"""Root scheduling boundary for unprivileged research imports; no publication or mail."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

CONFIG = Path('/etc/illoura-market/refresh.json')
LOCK = Path('/var/lib/illoura-market-refresh/refresh.lock')
STATE = Path('/var/lib/illoura-research-refresh')
HARD_LIMIT = 45 * 60


def blackout(now=None):
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('Australia/Sydney'))
    return 330 <= local.hour * 60 + local.minute < 570


def atomic_status(value, directory=STATE):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.status-', dir=directory)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, directory / 'status.json')


def configuration(path):
    st = path.stat()
    if st.st_uid != 0 or st.st_mode & 0o077 or not path.is_file() or path.is_symlink():
        raise ValueError('Configuration must be root-owned mode 0600')
    data = json.loads(path.read_text(encoding='utf-8'))
    if set(data) != {'data_root', 'build', 'tools'}:
        raise ValueError('Unexpected configuration fields')
    root = Path(data['data_root']).resolve(strict=True)
    build = Path(data['build']).resolve(strict=True)
    if not build.is_relative_to(root / 'builds'):
        raise ValueError('Unexpected research build path')
    return root, build


def run_application(root, build, timeout=HARD_LIMIT):
    deadline = time.monotonic() + timeout
    # The complete descendant tree shares a process group, including runuser.
    argv = ['nice', '-n', '15', 'ionice', '-c', '2', '-n', '7', 'runuser', '-u', 'ubuntu', '--',
            str(root / 'venv/bin/python'), '-m', 'market_intelligence.research_refresh',
            '--environment', str(root / 'database.env'), '--data-root', str(root)]
    # A temporary file bounds memory use even if application output misbehaves.
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(argv, cwd=build, stdout=output, stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic() - 5))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=max(0.01, min(5, deadline - time.monotonic())))
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=max(0.01, deadline - time.monotonic()))
            except ProcessLookupError:
                pass
            # Kill remaining descendants even if the parent exited on TERM.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            return dict(status='failed', reason='timeout_process_group_terminated')
        if process.returncode:
            return dict(status='failed', reason='application_failed')
        output.seek(0)
        payload = output.read(256_001)
        if len(payload) > 256_000:
            raise ValueError('Application summary exceeds bounds')
        data = json.loads(payload)
        statuses = {'latest_available', 'new_edition_pending', 'check_overdue', 'blocked', 'failed', 'manual_refresh_needed'}
        sources = data.get('freshness', {}).get('sources', [])
        summary = {r['source']: r['status'] for r in sources[:32]
                   if r.get('source') in SOURCE_NAMES and r.get('status') in statuses}
        return dict(status='complete' if data.get('status') == 'complete' else 'failed', freshness=summary)


SOURCE_NAMES = frozenset(('abn', 'asic_companies', 'asic_business_names', 'acnc', 'agor', 'government_directory',
    'oric', 'jsa_ivi', 'jsa_projections', 'jsa_shortages', 'jsa_reos', 'jsa_tnv', 'aps_employment',
    'aged_care_star_ratings', 'rap_directory', 'wa_contracts', 'procurement', 'tender_feed'))


def refresh(config=CONFIG, timeout=HARD_LIMIT, now=None):
    import fcntl
    if not 10 <= timeout <= HARD_LIMIT:
        raise ValueError('Research refresh hard limit is 45 minutes')
    if blackout(now):
        return dict(status='skipped', reason='sydney_brief_blackout')
    if not config.exists():
        return dict(status='disabled', reason='not_configured')
    root, build = configuration(config)
    LOCK.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with LOCK.open('a') as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='busy', reason='shared_morning_refresh_lock')
        # A queued/manual start can cross the window boundary while acquiring lock.
        if blackout(now):
            return dict(status='skipped', reason='sydney_brief_blackout')
        return run_application(root, build, timeout)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout', type=int, default=HARD_LIMIT)
    args = parser.parse_args(argv)
    try:
        result = refresh(timeout=args.timeout)
    except Exception as exc:
        result = dict(status='failed', reason=type(exc).__name__)
    atomic_status(dict(result, checked_at=datetime.now(timezone.utc).isoformat()))
    print(json.dumps(result))
    return 1 if result['status'] == 'failed' else 0


if __name__ == '__main__':
    raise SystemExit(main())
