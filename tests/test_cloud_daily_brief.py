"""Daily brief selection, editorial notes and send window, with synthetic data only."""
import importlib.util
import sqlite3
from contextlib import closing
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    'daily_brief', Path(__file__).resolve().parents[1] / 'deploy/illoura_daily_brief.py')
brief = importlib.util.module_from_spec(spec)
spec.loader.exec_module(brief)

NOW = datetime(2026, 11, 12, 20, 0, tzinfo=timezone.utc)  # 07:00 Sydney (AEDT) on 13 November


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'intelligence.db'
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('CREATE TABLE opportunities(source TEXT, source_id TEXT, closes_at TEXT, title TEXT, '
                       'buyer TEXT, url TEXT, status_effective TEXT, is_relevant INTEGER)')

    def add(self, key, closes, source='BuyICT', status='Open', relevant=1, title='Role', buyer='Agency'):
        with closing(sqlite3.connect(self.db)) as db, db:
            db.execute('INSERT INTO opportunities VALUES (?,?,?,?,?,?,?,?)',
                       (source, key, closes, title, buyer, 'https://www.buyict.gov.au/x?' + key, status, relevant))

    def at(self, hours):
        return (NOW + timedelta(hours=hours)).isoformat()

    def test_labour_hire_closing_soonest_within_window(self):
        self.add('LH-1', self.at(10))            # too soon (< 18 h)
        self.add('LH-2', self.at(48))
        self.add('LH-3', self.at(24))
        self.add('LH-4', self.at(24 * 30))       # beyond 21 days
        self.add('LH-5', self.at(30), status='Closed')
        self.add('LH-6', self.at(30), relevant=0)
        self.add('LH-7', self.at(30), source='AusTender')
        self.add('PCS-1', self.at(20))
        self.add('LH-8', self.at(36))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-3', 'LH-8', 'LH-2'])

    def test_offsets_are_compared_as_instants_not_text(self):
        # 06:00 +11:00 on the 14th is 19:00 UTC on the 13th: 23 h after NOW, so eligible...
        self.add('LH-A', '2026-11-14T06:00:00+11:00')
        # ...while 01:00 +00:00 on the 13th is only 5 h after NOW, though it sorts later as text.
        self.add('LH-B', '2026-11-13T01:00:00+00:00')
        self.add('LH-C', self.at(40))
        self.add('LH-D', self.at(41))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-A', 'LH-C', 'LH-D'])

    def test_professional_services_fill_when_labour_hire_is_scarce(self):
        self.add('LH-1', self.at(30))
        self.add('PCS-1', self.at(20))
        self.add('PCS-2', self.at(25))
        self.assertEqual(brief.select_ids(self.db, NOW), ['LH-1', 'PCS-1', 'PCS-2'])

    def test_malformed_close_times_are_ignored(self):
        self.add('LH-1', 'Friday, 27 November 2026')
        self.add('LH-2', '')
        self.add('LH-3', '2026-11-20T10:00:00')  # no offset: ambiguous, ignored
        self.assertEqual(brief.select_ids(self.db, NOW), [])

    def test_new_since_baseline(self):
        self.add('LH-1', self.at(30), title='Data � engineer')
        self.add('LH-2', self.at(40))
        rows = brief.new_since_baseline(self.db, ['LH-2'])
        self.assertEqual([r[0] for r in rows], ['LH-1'])
        self.assertIn('Canberra', rows[0][3])


class EditorialTests(unittest.TestCase):
    def record(self, description='Deliver services.'):
        return {'content': {'description': description, 'mainDetails': [], 'rfqOpen': True}}

    def test_notes_have_the_fields_the_composer_reads(self):
        report = {'results': [{'spider': 'buy_ict', 'status': 'OK'}]}
        notes = brief.editorial_notes(date(2026, 11, 13), {'LH-1': self.record()},
                                      [('LH-9', 'Role', 'Agency', '20 November, 02:00 PM Canberra', 'https://x')],
                                      report)
        for field in ('edition_date', 'subject', 'preheader', 'introduction', 'opportunities',
                      'additional_sections', 'coverage_note'):
            self.assertIn(field, notes)
        self.assertEqual(notes['edition_date'], '2026-11-13')
        self.assertIn('13 November 2026', notes['subject'])
        self.assertIn('1 open request ', notes['subject'] + ' ')
        self.assertIn('automatic', notes['coverage_note'].lower())
        self.assertEqual(notes['additional_sections'][0]['items'][0]['links'][0]['url'], 'https://x')
        self.assertIsInstance(notes['opportunities']['LH-1'], list)

    def test_restricted_listing_text_adds_an_access_warning(self):
        report = {'results': []}
        notes = brief.editorial_notes(date(2026, 11, 13), {
            'LH-1': self.record('This request is only open to selected sellers on the panel.'),
            'LH-2': self.record()}, [], report)
        self.assertTrue(any('access' in n for n in notes['opportunities']['LH-1']))
        self.assertFalse(any('access' in n for n in notes['opportunities']['LH-2']))
        self.assertEqual(notes['additional_sections'], [])

    def test_source_gaps_are_named(self):
        notes = brief.editorial_notes(date(2026, 11, 13), {}, [], {'results': [{'spider': 'nsw_tender', 'status': 'FAILED'}]})
        self.assertIn('nsw_tender', notes['coverage_note'])
        self.assertIn('0 open requests', notes['subject'])

    def test_clean_removes_replacement_characters(self):
        self.assertEqual(brief.clean('A � B'), 'A - B')

    def test_still_open_requires_open_listing_and_two_hours(self):
        def closing(_text):
            return NOW + timedelta(hours=5)
        listing = {'content': {'mainDetails': [{'label': 'RFQ closing date', 'value': 'x'}], 'rfqOpen': True}}
        self.assertTrue(brief.still_open(listing, NOW, closing))
        listing['content']['rfqOpen'] = False
        self.assertFalse(brief.still_open(listing, NOW, closing))
        listing['content']['rfqOpen'] = True
        self.assertFalse(brief.still_open(listing, NOW + timedelta(hours=4), closing))


class SendWindowTests(unittest.TestCase):
    def test_send_at_0815_sydney_with_noon_cutoff(self):
        target, cutoff = brief.send_window(NOW)
        self.assertEqual((target.hour, target.minute, target.day), (8, 15, 13))
        self.assertEqual((cutoff.hour, cutoff.minute), (12, 0))
        self.assertEqual(target.utcoffset(), timedelta(hours=11))  # AEDT in November
        self.assertEqual(target.astimezone(timezone.utc).hour, 21)


if __name__ == '__main__':
    unittest.main()
