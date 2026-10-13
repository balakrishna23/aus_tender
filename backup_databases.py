#!/usr/bin/env python3
"""Consistent copies of every SQLite database, kept for KEEP_DAYS.

Uses SQLite's online backup API, so it is safe while a spider is writing.
The contract API database (2.6 GB, and re-downloadable from the public API) is copied once a
week instead, and only the last two weekly copies are kept.
The reviewed input folders (MMR review, supplier contact registers) are git-ignored and hand-made,
so each daily backup carries a copy of them too.

Usage:
  python backup_databases.py              # backups/daily-YYYYMMDD/ and backups/weekly-YYYY-Www/
  python backup_databases.py --keep 30
"""
import argparse
import shutil
import sqlite3
from contextlib import closing
from datetime import date
import time

from project_paths import PROJECT_DIR, database_directory

WEEKLY = {'contract_api.db'}
WEEKLY_KEEP = 2
INPUTS = ('inputs', 'local_assistant/inputs')


def copy(source, destination):
    """Write to a temporary name first, so an interrupted copy never looks finished."""
    partial = destination.with_name(destination.name + '.partial')
    partial.unlink(missing_ok=True)
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(partial)) as dst:
        src.backup(dst)
    partial.replace(destination)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--keep', type=int, default=14, help='Days of backups to keep')
    args = parser.parse_args(argv)

    root = PROJECT_DIR / 'backups'
    year, week, _ = date.today().isocalendar()
    daily, weekly = root / time.strftime('daily-%Y%m%d'), root / f'weekly-{year}-W{week:02d}'
    for source in sorted(database_directory().glob('*.db')):
        target = weekly if source.name in WEEKLY else daily
        if target is weekly and (weekly / source.name).exists():
            continue
        target.mkdir(parents=True, exist_ok=True)
        copy(source, target / source.name)
        print(f'Backed up {source.name} to {target.name}')
    for folder in INPUTS:
        source = PROJECT_DIR / folder
        if source.is_dir():
            shutil.copytree(source, daily / 'inputs' / folder.replace('/', '_'), dirs_exist_ok=True)
            print(f'Backed up {folder}/ to {daily.name}')

    cutoff = time.time() - args.keep * 86400
    for old in root.glob('daily-*'):
        if old.is_dir() and old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)
            print(f'Removed old backup {old.name}')
    for old in sorted(p for p in root.glob('weekly-*') if p.is_dir())[:-WEEKLY_KEEP]:
        shutil.rmtree(old, ignore_errors=True)
        print(f'Removed old backup {old.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
