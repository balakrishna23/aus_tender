import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import gspread

import publish_sheets

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log


class PublishSheetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.temp.name,
                                          'GSHEETS_FOLDER_ID': 'https://drive.google.com/drive/folders/FOLDER123?usp=sharing'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_folder_link_is_accepted(self):
        self.assertEqual(publish_sheets.folder_id(), 'FOLDER123')

    def test_created_once_then_reused(self):
        gc = MagicMock()
        gc.create.return_value = MagicMock(id='NEW1', url='https://sheet/NEW1', title='t')
        registry = publish_sheets.load_registry()
        publish_sheets.spreadsheet_for(gc, 'buyict', registry)
        gc.create.assert_called_once_with('BuyICT - Opportunities', folder_id='FOLDER123')
        self.assertEqual(publish_sheets.load_registry(), {'buyict': 'NEW1'})
        gc.reset_mock()
        publish_sheets.spreadsheet_for(gc, 'buyict', publish_sheets.load_registry())
        gc.open_by_key.assert_called_once_with('NEW1')
        gc.create.assert_not_called()

    def test_deleted_sheet_is_recreated(self):
        gc = MagicMock()
        gc.open_by_key.side_effect = gspread.SpreadsheetNotFound
        gc.create.return_value = MagicMock(id='NEW2', url='u', title='t')
        publish_sheets.spreadsheet_for(gc, 'act', {'act': 'GONE'})
        self.assertEqual(publish_sheets.load_registry()['act'], 'NEW2')

    def test_missing_folder_is_a_clear_error(self):
        with patch.dict(os.environ, {'GSHEETS_FOLDER_ID': ''}):
            with self.assertRaisesRegex(RuntimeError, 'GSHEETS_FOLDER_ID'):
                publish_sheets.spreadsheet_for(MagicMock(), 'act', {})

    def test_long_cells_are_truncated(self):
        import pandas as pd
        frame = publish_sheets._truncate(pd.DataFrame({'a': ['x' * 60000, 'short']}))
        self.assertLess(len(frame['a'][0]), 50000)
        self.assertEqual(frame['a'][1], 'short')


class ClaimFromFolderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patcher = patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.temp.name, 'GSHEETS_FOLDER_ID': 'F1'})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _quota_client(self, files):
        gc = MagicMock()
        response = MagicMock(status_code=403)
        response.json.return_value = {'error': {'code': 403, 'message': "The user's Drive storage quota has been exceeded."}}
        gc.create.side_effect = gspread.exceptions.APIError(response)
        gc.list_spreadsheet_files.return_value = files
        gc.open_by_key.side_effect = lambda sid: MagicMock(id=sid, title='Untitled spreadsheet', url=f'u/{sid}')
        return gc

    def test_blank_sheets_are_claimed_and_renamed(self):
        gc = self._quota_client([{'id': 'A', 'name': 'Untitled spreadsheet'},
                                 {'id': 'B', 'name': 'Untitled spreadsheet'}])
        registry = {}
        first = publish_sheets.spreadsheet_for(gc, 'act', registry)
        second = publish_sheets.spreadsheet_for(gc, 'nsw', registry)
        self.assertEqual((first.id, second.id), ('A', 'B'))  # never the same sheet twice
        first.update_title.assert_called_once_with('ACT - Tenders ACT')

    def test_no_spare_sheet_says_what_to_do(self):
        gc = self._quota_client([])
        with self.assertRaisesRegex(RuntimeError, 'create a blank Google Sheet'):
            publish_sheets.spreadsheet_for(gc, 'act', {})


if __name__ == '__main__':
    unittest.main()


class SheetFilterTests(unittest.TestCase):
    """Sheets show open/active items plus the last year; the databases keep everything."""

    def test_recent_keeps_last_year_or_kept_rows(self):
        import pandas as pd
        from datetime import date, timedelta
        old = (date.today() - timedelta(days=800)).strftime('%d-%b-%Y')
        new = (date.today() - timedelta(days=30)).strftime('%d-%b-%Y')
        frame = pd.DataFrame({'publish_date': [old, new, old, ''], 'close_date': ['', '', new, ''],
                              'status': ['closed', 'closed', 'closed', 'open']})
        kept = publish_sheets._recent(frame, 'publish_date', 'close_date', keep=publish_sheets._status_is('open'))
        self.assertEqual(list(kept.index), [0, 1, 2])
        self.assertEqual(kept['status'].tolist(), ['closed', 'closed', 'open'])
        self.assertEqual(kept['publish_date'].tolist(), [new, old, ''])

    def test_standing_offer_active_by_period_end(self):
        self.assertTrue(publish_sheets._offer_active({'standing_offer_period': '1-Jul-2024 to 30-Jun-2099'}))
        self.assertFalse(publish_sheets._offer_active({'standing_offer_period': '1-Jul-2008 to 31-Dec-2017'}))
        self.assertFalse(publish_sheets._offer_active({'standing_offer_period': ''}))

    def test_planned_procurements_seen_in_latest_crawl(self):
        import pandas as pd
        frame = pd.DataFrame({'scraped_at': ['2026-10-04T02:15:00', '2026-10-04T02:14:00',
                                             '2026-09-23T10:00:00', None]})
        self.assertEqual(len(publish_sheets._still_listed(frame)), 2)
