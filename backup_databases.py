#!/usr/bin/env python3
"""Consistent copies of every SQLite database, kept for KEEP_DAYS.

Uses SQLite's online backup API, so it is safe while a spider is writing.

Usage:
  python backup_databases.py              # backups/daily-YYYYMMDD/
  python backup_databases.py --keep 30
"""
import argparse
import shutil
import sqlite3
from contextlib import closing
import time

from project_paths import PROJECT_DIR, database_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--keep', type=int, default=14, help='Days of backups to keep')
    args = parser.parse_args()

    root = PROJECT_DIR / 'backups'
    target = root / time.strftime('daily-%Y%m%d')
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(database_directory().glob('*.db')):
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target / source.name)) as dst:
            src.backup(dst)
        print(f'Backed up {source.name}')

    cutoff = time.time() - args.keep * 86400
    for old in root.glob('daily-*'):
        if old.is_dir() and old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)
            print(f'Removed old backup {old.name}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
