#!/usr/bin/env python3
"""Build ../aus_tender_vps.tar.gz for the VPS: code, .env, Google key and the current databases.

  python deploy/package_for_vps.py
Then:
  scp ../aus_tender_vps.tar.gz root@YOUR_VPS_IP:/root/
  ssh root@YOUR_VPS_IP "mkdir -p aus_tender && tar -xzf aus_tender_vps.tar.gz -C aus_tender && bash aus_tender/deploy/setup_vps.sh"
"""
import io
import sqlite3
import sys
import tarfile
import tempfile
from contextlib import closing
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT))
from project_paths import database_directory  # noqa: E402

SKIP_DIRS = {'.venv', '.browsers', '__pycache__', '.git', 'logs', 'backups', 'exports',
             'databases', 'databases_live', '.playwright-cli', '.playwright'}
SKIP_SUFFIXES = {'.png', '.html', '.gz', '.db', '.db-shm', '.db-wal', '.pyc', '.log'}


def _key_file_in_use():
    """The Google key .env points at (relative name), so other keys and JSON dumps stay home."""
    from dotenv import dotenv_values
    values = dotenv_values(PROJECT / '.env')
    for name in ('GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT', 'GSHEETS_SERVICE_ACCOUNT_FILE'):
        value = (values.get(name) or '').strip().strip('"').strip("'")
        if value.endswith('.json'):
            return Path(value).name
    return None


KEY_FILE = _key_file_in_use()


def include(path: Path) -> bool:
    rel = path.relative_to(PROJECT)
    if set(rel.parts) & SKIP_DIRS or path.suffix in SKIP_SUFFIXES:
        return False
    if path.suffix == '.json' and len(rel.parts) == 1:
        return path.name == KEY_FILE
    return True


def main() -> int:
    out = PROJECT.parent / 'aus_tender_vps.tar.gz'
    with tempfile.TemporaryDirectory() as staging, tarfile.open(out, 'w:gz') as tar:
        for path in sorted(PROJECT.rglob('*')):
            if path.is_file() and include(path):
                info = tar.gettarinfo(path, arcname=path.relative_to(PROJECT).as_posix())
                if path.suffix == '.sh':
                    info.mode = 0o755
                with open(path, 'rb') as f:
                    if path.suffix in ('.sh', '.service', '.timer'):
                        data = f.read().replace(b'\r\n', b'\n')  # bash/systemd need LF
                        info.size = len(data)
                        tar.addfile(info, io.BytesIO(data))
                    else:
                        tar.addfile(info, f)
        # Consistent copies via SQLite's backup API, safe even while a run is writing.
        for source in sorted(database_directory().glob('*.db')):
            copy = Path(staging) / source.name
            with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(copy)) as dst:
                src.backup(dst)
            tar.add(copy, arcname=f'databases/{source.name}')
            print(f'  + databases/{source.name}')
        # Which Google Sheet holds which dataset, so the server refreshes the same sheets.
        registry = database_directory() / 'sheets_registry.json'
        if registry.exists():
            tar.add(registry, arcname='databases/sheets_registry.json')
            print('  + databases/sheets_registry.json')
    print(f'Created {out} ({out.stat().st_size / 1e6:.1f} MB)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
