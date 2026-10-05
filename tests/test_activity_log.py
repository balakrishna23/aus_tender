import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

import activity_log


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.db = os.path.join(self.dir.name, 'activity.db')
        env = patch.dict(os.environ, {'ACTIVITY_DB': self.db, 'ACTIVITY_RUN_ID': 'run-1'})
        env.start()
        self.addCleanup(env.stop)

    def rows(self, sql):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute(sql).fetchall()

    def test_run_scrape_items_and_shares_are_recorded(self):
        activity_log.start_run('run-1', ['buy_ict'])
        activity_log.record_items([activity_log.item_event(
            'buy_ict', 'new', {'rfq_id': 'LH-1', 'title': '2 Data Engineers', 'url': 'https://x/1'})])
        activity_log.record_scrape('run-1', {'spider': 'buy_ict', 'status': 'OK', 'new': 1, 'after': 10})
        activity_log.record_share('buyict', 'BuyICT', 'https://sheet', {'opportunities': 10, 'other': 2})
        activity_log.finish_run('run-1', 'ok', 'done')
        self.assertEqual(self.rows('SELECT run_id, dataset, change, reference, title, url FROM items'),
                         [('run-1', 'buy_ict', 'new', 'LH-1', '2 Data Engineers', 'https://x/1')])
        self.assertEqual(self.rows('SELECT status, finished_at IS NOT NULL FROM runs'), [('ok', 1)])
        self.assertEqual(self.rows('SELECT spider, new, total_after FROM scrapes'), [('buy_ict', 1, 10)])
        self.assertEqual(self.rows('SELECT dataset, total_rows, status FROM shares'), [('buyict', 12, 'ok')])

    def test_access_tracks_people_added_and_removed(self):
        alice = {'emailAddress': 'Alice@x.com', 'role': 'owner', 'type': 'user', 'displayName': 'Alice'}
        bob = {'emailAddress': 'bob@x.com', 'role': 'reader', 'type': 'user'}
        activity_log.record_access('releases', 'sheet1', [alice, bob])
        activity_log.record_access('releases', 'sheet1', [alice])
        self.assertEqual(self.rows('SELECT email, removed_at IS NULL FROM access ORDER BY email'),
                         [('alice@x.com', 1), ('bob@x.com', 0)])
        activity_log.record_access('releases', 'sheet1', [alice, bob])  # shared again
        self.assertEqual(self.rows('SELECT COUNT(*) FROM access WHERE removed_at IS NULL'), [(2,)])

    def test_logging_failure_never_breaks_the_caller(self):
        with patch.object(activity_log, 'connect', side_effect=sqlite3.OperationalError('disk full')):
            with self.assertLogs('activity_log', level='WARNING'):
                activity_log.record_email('a@b.com', 'Hi', 'k', 'sent')
