"""Part B: notice identities must survive changed URLs and partial updates."""
import tempfile
import unittest
from database_manager import DatabaseManager


class UpsertIdentity(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = DatabaseManager(tmp.name)
        self.db.initialize_database('aus_tender')

    def rows(self):
        with self.db.get_connection('aus_tender') as conn:
            return [dict(row) for row in conn.execute('SELECT atm_id, agency, url, description FROM tenders')]

    def test_url_collision_cannot_replace_another_notice_identity(self):
        old = {'ATM ID': 'A1', 'Agency': 'One', 'URL': 'https://example.test/shared'}
        self.assertEqual(self.db.insert_item('aus_tender', old), 'new')
        self.assertFalse(self.db.insert_item('aus_tender', {**old, 'ATM ID': 'B2'}))
        self.assertEqual(self.rows()[0]['atm_id'], 'A1')

    def test_same_notice_new_url_is_one_row(self):
        old = {'ATM ID': 'A1', 'Agency': 'One', 'URL': 'https://example.test/old'}
        self.db.insert_item('aus_tender', old)
        self.assertEqual(self.db.insert_item('aus_tender', {**old, 'URL': 'https://example.test/new'}), 'updated')
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]['url'], 'https://example.test/new')

    def test_partial_update_preserves_omitted_fields(self):
        old = {'ATM ID': 'A1', 'Agency': 'One', 'URL': 'https://example.test/a', 'Description': 'Important'}
        self.db.insert_item('aus_tender', old)
        partial = {key: value for key, value in old.items() if key != 'Description'}
        self.assertEqual(self.db.insert_item('aus_tender', {**partial, 'Agency': 'Two'}), 'updated')
        self.assertEqual(self.rows()[0]['description'], 'Important')
