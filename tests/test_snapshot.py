"""Data snapshots: secrets never packed; a restore brings back the data, not the code, and keeps local databases."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import zipfile
from contextlib import closing
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'deploy'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import package_snapshot as pack  # noqa: E402
import restore_snapshot as restore  # noqa: E402


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.project = self.root / 'project'
        (self.project / 'databases').mkdir(parents=True)
        (self.project / 'inputs' / 'mmr').mkdir(parents=True)
        (self.project / 'inputs' / 'mmr' / 'review.csv').write_text('a,b\n', encoding='utf-8')
        (self.project / 'databases' / 'sheets_registry.json').write_text('{}', encoding='utf-8')
        (self.project / 'run.py').write_text('print(1)\n', encoding='utf-8')
        (self.project / '.env').write_text('SECRET=1\n', encoding='utf-8')
        for name in ('intelligence.db', 'sheet_imports.db'):
            with closing(sqlite3.connect(self.project / 'databases' / name)) as conn:
                conn.execute('CREATE TABLE t (x)')
                conn.execute('INSERT INTO t VALUES (1)')
                conn.commit()
        git = ['git', '-C', str(self.project)]
        subprocess.run(git + ['init', '-q'], check=True)
        subprocess.run(git + ['add', 'run.py'], check=True)
        subprocess.run(git + ['-c', 'user.name=t', '-c', 'user.email=t@example.com', 'commit', '-qm', 'x'], check=True)

    def test_pack_holds_data_and_code_but_no_secrets_or_sheet_mirror(self):
        out = self.root / 'snap.zip'
        names = pack.build(out, project=self.project, databases=self.project / 'databases')
        self.assertIn('databases/intelligence.db', names)
        self.assertIn('databases/sheets_registry.json', names)
        self.assertIn('inputs/mmr/review.csv', names)
        self.assertIn('run.py', names)
        self.assertNotIn('databases/sheet_imports.db', names)
        self.assertFalse([n for n in zipfile.ZipFile(out).namelist() if n.endswith('.env')])

    def test_restore_copies_data_only_and_keeps_existing_databases(self):
        out = self.root / 'snap.zip'
        pack.build(out, project=self.project, databases=self.project / 'databases')
        clone = self.root / 'clone'
        (clone / 'databases').mkdir(parents=True)
        (clone / 'databases' / 'intelligence.db').write_bytes(b'local')
        restored, kept = restore.restore(out, project=clone)
        self.assertIn('inputs/mmr/review.csv', restored)
        self.assertEqual(kept, ['databases/intelligence.db'])
        self.assertEqual((clone / 'databases' / 'intelligence.db').read_bytes(), b'local')
        self.assertFalse((clone / 'run.py').exists())
        restore.restore(out, project=clone, force=True)
        with closing(sqlite3.connect(clone / 'databases' / 'intelligence.db')) as conn:
            self.assertEqual(conn.execute('SELECT x FROM t').fetchone(), (1,))

    def test_public_snapshot_removes_personal_and_internal_data_but_keeps_the_layout(self):
        dbs = self.project / 'databases'
        with closing(sqlite3.connect(dbs / 'aus_tender.db')) as conn:
            conn.execute('CREATE TABLE tenders (atm_id TEXT, description TEXT, contact_name TEXT, '
                         'contact_email TEXT, contact_phone TEXT, all_details TEXT, value REAL)')
            conn.execute('INSERT INTO tenders VALUES (?,?,?,?,?,?,?)', (
                'ATM1', 'Questions to jane.citizen@agency.gov.au or (02) 6289 1134; ABN 51 824 753 556',
                'Jane Citizen', 'jane.citizen@agency.gov.au', '0412 345 678',
                '{"Agency": "Finance", "Contact Name": "Jane Citizen", "Contact Branch": "Procurement",'
                ' "Notes": {"Contact Phone": "0412345678", "Text": "mobile 0412 345 678, closes 02/08/2026"}}', 5.0))
            conn.commit()
        with closing(sqlite3.connect(dbs / 'organisations.db')) as conn:
            conn.execute('CREATE TABLE mmr_matches (organisation TEXT, supplier_abn TEXT, review TEXT, '
                         'reviewed_at TEXT, note TEXT)')
            conn.execute("INSERT INTO mmr_matches VALUES ('DXC', '1', 'confirmed', '2026-10-07', 'internal')")
            conn.commit()
        out = self.root / 'public.zip'
        names = pack.build(out, project=self.project, databases=dbs, public=True)
        self.assertIn('DATA_README.md', names)
        self.assertIn('databases/aus_tender.db', names)
        self.assertNotIn('databases/intelligence.db', names)  # engine views are rebuilt locally
        self.assertNotIn('inputs/mmr/review.csv', names)
        self.assertNotIn('databases/sheets_registry.json', names)
        clone = self.root / 'clone'
        restore.restore(out, project=clone)
        with closing(sqlite3.connect(clone / 'databases' / 'aus_tender.db')) as conn:
            row = conn.execute('SELECT * FROM tenders').fetchone()
        atm, description, name, email, phone, details, value = row
        self.assertEqual((atm, name, email, phone, value), ('ATM1', None, None, None, 5.0))
        self.assertEqual(description, 'Questions to [email removed] or [phone removed]; ABN 51 824 753 556')
        self.assertEqual(json.loads(details), {'Agency': 'Finance', 'Contact Branch': 'Procurement',
                                               'Notes': {'Text': 'mobile [phone removed], closes 02/08/2026'}})
        with closing(sqlite3.connect(clone / 'databases' / 'organisations.db')) as conn:
            self.assertEqual(conn.execute('SELECT * FROM mmr_matches').fetchone(), ('DXC', '1', None, None, None))
        with self.assertRaises(SystemExit):
            pack.build(self.root / 'x.zip', docs=str(self.root), project=self.project, databases=dbs, public=True)

    def test_a_secret_is_refused(self):
        self.assertTrue(pack.is_secret('dazzling-tiger-470805.json'))
        self.assertTrue(pack.is_secret('.env'))
        self.assertFalse(pack.is_secret('.env.example'))


if __name__ == '__main__':
    unittest.main()
