"""Part B: generation verification is the last gate before rollback serves data."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
import test_cloud_ops as fixtures

ops = fixtures.ops


class GenerationVerification(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def manifest(self, datasets):
        (self.root / 'manifest.json').write_text(json.dumps({'generation_id': 'synthetic', 'datasets': datasets}))

    def test_empty_generation_is_not_rollback_ready(self):
        self.manifest({})
        with self.assertRaises(ValueError):
            ops.verify_generation(self.root)

    def test_required_manifest_tables_must_exist(self):
        path = self.root / 'contract_api.db'
        with closing(sqlite3.connect(path)) as db:
            db.execute('CREATE TABLE unrelated(id TEXT)')
            db.commit()
        self.manifest({'contract_api': {'file': path.name, 'sha256': ops.sha(path), 'tables': {'contracts': 1}}})
        with self.assertRaises(ValueError):
            ops.verify_generation(self.root)

    def test_changed_database_hash_is_refused(self):
        path = self.root / 'contract_api.db'
        path.write_bytes(b'broken database')
        self.manifest({'contract_api': {'file': path.name, 'sha256': '0' * 64}})
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            ops.verify_generation(self.root)
