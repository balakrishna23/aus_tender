import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import build_releases
from database_manager import DatabaseManager

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log


class ReleasesFeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.db = DatabaseManager(self.temp.name)
        self.exports = Path(self.temp.name) / 'exports'
        for name in ('aus_tender', 'buy_ict', 'act_tender', 'nsw_tender', 'planned_procurement'):
            self.db.initialize_database(name)

    def add_atm(self, atm_id, published, closes='20-Dec-2099 2:00 pm', url_kind='Show'):
        self.db.insert_item('aus_tender', {
            'ATM ID': atm_id, 'Agency': 'Agency', 'Service Name': f'Tender {atm_id}',
            'Publish Date': published, 'Close Date & Time': closes,
            'URL': f'https://www.tenders.gov.au/Atm/{url_kind}/{atm_id}'})

    def build(self, since='2026-10-02'):
        return build_releases.build(since, export_dir=self.exports)

    def test_only_releases_from_feed_start(self):
        self.add_atm('OLD', '1-Oct-2026')
        self.add_atm('NEW', '2-Oct-2026')
        self.db.insert_item('buy_ict', {'url': 'https://b/1', 'rfq_id': 'LH-1', 'title': 'Analyst',
                                        'rfq_published_date': 'Friday, 02 October 2026',
                                        'rfq_closing_date': 'Friday, 16 October 2099', 'status': 'open'})
        result = self.build()
        self.assertEqual(sorted(r['source_id'] for r in result['new']), ['LH-1', 'NEW'])
        exported = json.loads(next(self.exports.glob('new_*.json')).read_text())
        self.assertEqual(len(exported), 2)

    def test_rebuild_adds_nothing_and_tracks_changes(self):
        self.add_atm('NEW', '2-Oct-2026')
        self.assertEqual(len(self.build()['new']), 1)
        again = self.build()
        self.assertEqual((len(again['new']), len(again['changed'])), (0, 0))
        self.add_atm('NEW', '2-Oct-2026', closes='5-Jan-2100 2:00 pm')
        changed = self.build()
        self.assertEqual((len(changed['new']), len(changed['changed'])), (0, 1))

    def test_closed_status(self):
        self.add_atm('SHUT', '2-Oct-2026', url_kind='ShowClosed')
        self.add_atm('PAST', '2-Oct-2026', closes='3-Oct-2026 2:00 pm')
        with patch.object(build_releases, 'date') as fake_date:
            fake_date.today.return_value = date(2026, 10, 10)
            fake_date.side_effect = lambda *a: date(*a)
            statuses = {r['source_id']: r['status'] for r in self.build()['new']}
        self.assertEqual(statuses, {'SHUT': 'closed', 'PAST': 'closed'})

    def test_date_formats(self):
        for text, expected in [('24-Sep-2026', date(2026, 9, 24)),
                               ('Monday, 21 September 2026', date(2026, 9, 21)),
                               ('Thu, 02 July 2026 12:30 pm', date(2026, 7, 2)),
                               ('2026-10-02 15:08:04', date(2026, 10, 2)), ('', None)]:
            with self.subTest(text=text):
                self.assertEqual(build_releases.parse_day(text), expected)


if __name__ == '__main__':
    unittest.main()
