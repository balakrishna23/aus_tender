"""The engine-views spreadsheet is optional until a blank sheet exists for it."""
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import publish_sheets  # noqa: E402
import action_tracker
import os
import sqlite3
import tempfile
from contextlib import closing
from datetime import date, timedelta
import pandas as pd
import build_intelligence


class OptionalSheetTests(unittest.TestCase):
    def run_publish(self, error):
        def spreadsheet_for(gc, key, registry):
            if key == 'intelligence':
                raise RuntimeError(error)
            return Mock(url=f'https://sheet/{key}')
        with patch.object(publish_sheets, 'client'), patch.object(publish_sheets, 'load_registry', return_value={}), \
                patch.object(publish_sheets, 'spreadsheet_for', side_effect=spreadsheet_for), \
                patch.object(publish_sheets, 'publish'), patch.object(publish_sheets, '_track_access'), \
                patch.object(publish_sheets.activity_log, 'record_share'), \
                patch.dict(publish_sheets.DATASETS, {'releases': ('R', lambda: {}),
                                                     'intelligence': ('I', lambda: {})}, clear=True):
            return publish_sheets.publish_all()

    def test_no_spare_sheet_is_skipped_not_failed(self):
        links = self.run_publish("No spare spreadsheet in the folder for 'Illoura - Engine Views': create one")
        self.assertEqual(links, {'releases': 'https://sheet/releases'})

    def test_other_errors_still_fail(self):
        with self.assertRaises(RuntimeError):
            self.run_publish('quota exceeded')


class TrackerSheetTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        env = patch.dict(os.environ, {'AUSTENDER_DB_DIR': directory.name})
        env.start()
        self.addCleanup(env.stop)

    def test_reads_before_build_writes_tracker_and_dropdowns(self):
        events = []
        book = Mock(url='https://sheet/intelligence')
        book.worksheet.return_value.id = 42
        book.worksheet.return_value.get_all_records.return_value = [{'action_id': 'A-test'}]
        book.fetch_sheet_metadata.return_value = {'sheets': [{'properties': {'sheetId': 42}}]}
        def record(conn, rows):
            events.append('read')
            self.assertEqual(rows, [{'action_id': 'A-test'}])
        def build():
            events.append('build')
            return {'Tracker': pd.DataFrame(columns=action_tracker.TRACKER_COLUMNS)}
        with patch.object(publish_sheets, 'client'), patch.object(publish_sheets, 'load_registry', return_value={}), \
                patch.object(publish_sheets, 'spreadsheet_for', return_value=book), \
                patch.object(publish_sheets, '_track_access'), patch.object(publish_sheets, 'publish') as publish, \
                patch.object(publish_sheets.activity_log, 'record_share'), \
                patch.object(action_tracker, 'record_edits', side_effect=record), \
                patch.dict(publish_sheets.DATASETS, {'intelligence': ('I', build)}, clear=True):
            publish_sheets.publish_all(['intelligence'])
        self.assertEqual(events, ['read', 'build'])
        self.assertIn('Tracker', publish.call_args.args[1])
        requests = book.batch_update.call_args.args[0]['requests']
        self.assertEqual(len(requests), 2)
        for request, column, values in zip(requests, ['status', 'stage'], [action_tracker.STATUSES, action_tracker.STAGES]):
            data = request['setDataValidation']
            self.assertEqual(data['range']['sheetId'], 42)
            self.assertEqual(data['range']['startColumnIndex'], action_tracker.TRACKER_COLUMNS.index(column))
            self.assertEqual([v['userEnteredValue'] for v in data['rule']['condition']['values']], values)

    def tracker_table(self, status_values):
        rule = lambda values: {'condition': {'type': 'ONE_OF_LIST',  # noqa: E731
                                             'values': [{'userEnteredValue': v} for v in values]}}
        return {'tableId': 't3', 'columnProperties': [
            {'columnIndex': 0, 'columnName': 'action_id'},
            {'columnIndex': 4, 'columnName': 'score', 'columnType': 'DOUBLE'},
            {'columnIndex': 7, 'columnName': 'status', 'columnType': 'DROPDOWN',
             'dataValidationRule': rule(status_values)},
            {'columnIndex': 8, 'columnName': 'stage', 'columnType': 'DROPDOWN',
             'dataValidationRule': rule(action_tracker.STAGES)}]}

    def table_book(self, table):
        book = Mock()
        book.worksheet.return_value.id = 42
        book.fetch_sheet_metadata.return_value = {'sheets': [
            {'properties': {'sheetId': 7}, 'tables': [{'tableId': 'other'}]},
            {'properties': {'sheetId': 42}, 'tables': [table]}]}
        return book

    def test_tracker_table_gets_dropdowns_through_the_table_not_set_data_validation(self):
        book = self.table_book(self.tracker_table(['proposed', 'accepted']))
        publish_sheets._tracker_validation(book)
        request = book.batch_update.call_args.args[0]['requests'][0]['updateTable']
        self.assertEqual(request['fields'], 'columnProperties')
        self.assertEqual(request['table']['tableId'], 't3')
        columns = {c['columnName']: c for c in request['table']['columnProperties']}
        self.assertEqual(set(columns), {'action_id', 'score', 'status', 'stage'})  # others kept
        self.assertEqual(columns['score']['columnType'], 'DOUBLE')
        self.assertEqual([v['userEnteredValue'] for v in columns['status']['dataValidationRule']['condition']['values']],
                         action_tracker.STATUSES)

    def test_tracker_table_already_right_is_left_alone(self):
        book = self.table_book(self.tracker_table(action_tracker.STATUSES))
        publish_sheets._tracker_validation(book)
        book.batch_update.assert_not_called()

    def test_missing_tracker_is_allowed_but_read_failure_prevents_overwrite(self):
        book = Mock()
        book.worksheet.side_effect = publish_sheets.gspread.WorksheetNotFound('Tracker')
        self.assertEqual(publish_sheets._read_tracker(book), [])
        book.worksheet.side_effect = RuntimeError('read unavailable')
        with patch.object(publish_sheets, 'client'), patch.object(publish_sheets, 'load_registry', return_value={}), \
                patch.object(publish_sheets, 'spreadsheet_for', return_value=book), \
                patch.object(publish_sheets, '_track_access'), patch.object(publish_sheets, 'publish') as publish, \
                patch.object(publish_sheets.activity_log, 'record_share'):
            with self.assertRaisesRegex(RuntimeError, 'read unavailable'):
                publish_sheets.publish_all(['intelligence'])
            publish.assert_not_called()

    def test_pull_uses_registered_sheet_and_is_noop_when_unregistered(self):
        with patch.object(publish_sheets, 'load_registry', return_value={}), patch.object(publish_sheets, 'client') as client:
            self.assertEqual(publish_sheets.pull_tracker(), 0)
            client.assert_not_called()
        with patch.object(publish_sheets, 'load_registry', return_value={'intelligence': 'registered'}), \
                patch.object(publish_sheets, 'client') as client, patch.object(action_tracker, 'record_edits', return_value=2):
            client.return_value.open_by_key.return_value.worksheet.return_value.get_all_records.return_value = []
            self.assertEqual(publish_sheets.pull_tracker(), 2)
            client.return_value.open_by_key.assert_called_once_with('registered')

    def test_publish_view_promotes_overdue_and_restores_after_revert(self):
        a = dict(priority='ACT NOW', score=86, organisation='DXC', action_type='Tier-2 offer to prime',
                 owner='Lane', why_now='Original hook', evidence='https://source', due='2026-10-09')
        with closing(action_tracker.connect()) as tracker:
            action_tracker.merge(tracker, [a], date.today())
            action_tracker.record_edits(tracker, [dict(action_id=a['action_id'], status='accepted',
                                                       follow_up=(date.today() - timedelta(days=1)).isoformat())])
        with closing(sqlite3.connect(self.directory / 'intelligence.db')) as conn:
            build_intelligence.write(conn, 'actions', [a], build_intelligence.ACTION_COLUMNS)
            for table in ['mmr_primes', 'mmr_candidates', 'renewals', 'panel_members', 'buyict_links']:
                build_intelligence.write(conn, table, [])
            conn.commit()
        with patch.object(build_intelligence, 'build_prime_briefs', return_value=[]):
            publish_sheets._refresh_today()
            with closing(sqlite3.connect(self.directory / 'intelligence.db')) as conn:
                self.assertEqual(conn.execute('SELECT priority FROM today').fetchone()[0], 'OVERDUE')
            with closing(action_tracker.connect()) as tracker:
                action_tracker.record_edits(tracker, [dict(action_id=a['action_id'], status='proposed', follow_up='')])
            publish_sheets._refresh_today()
            with closing(sqlite3.connect(self.directory / 'intelligence.db')) as conn:
                self.assertEqual(conn.execute('SELECT priority, why_now FROM today').fetchone(), ('ACT NOW', 'Original hook'))


if __name__ == '__main__':
    unittest.main()
