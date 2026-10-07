"""Backups: small databases daily, the large contract API database weekly (last two kept)."""
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import backup_databases  # noqa: E402


class BackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / 'databases').mkdir()
        for name in ('organisations.db', 'contract_api.db'):
            with closing(sqlite3.connect(self.root / 'databases' / name)) as db:
                db.execute('CREATE TABLE t (x)')
                db.execute("INSERT INTO t VALUES ('kept')")
                db.commit()
        for patcher in (patch.object(backup_databases, 'PROJECT_DIR', self.root),
                        patch.dict(os.environ, {'AUSTENDER_DB_DIR': str(self.root / 'databases')})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_large_database_is_copied_once_a_week(self):
        backup_databases.main([])
        year, week, _ = date.today().isocalendar()
        weekly = self.root / 'backups' / f'weekly-{year}-W{week:02d}' / 'contract_api.db'
        daily = list((self.root / 'backups').glob('daily-*/*.db'))
        self.assertEqual([p.name for p in daily], ['organisations.db'])
        with closing(sqlite3.connect(weekly)) as db:
            self.assertEqual(db.execute('SELECT x FROM t').fetchone()[0], 'kept')
        stamp = weekly.stat().st_mtime_ns
        backup_databases.main([])
        self.assertEqual(weekly.stat().st_mtime_ns, stamp)  # not copied again the same week
        self.assertEqual(list((self.root / 'backups').rglob('*.partial')), [])

    def test_only_the_last_two_weekly_copies_are_kept(self):
        for name in ('weekly-2026-W30', 'weekly-2026-W31', 'weekly-2026-W32'):
            (self.root / 'backups' / name).mkdir(parents=True)
        backup_databases.main([])
        year, week, _ = date.today().isocalendar()
        self.assertEqual(sorted(p.name for p in (self.root / 'backups').glob('weekly-*')),
                         ['weekly-2026-W32', f'weekly-{year}-W{week:02d}'])


if __name__ == '__main__':
    unittest.main()
