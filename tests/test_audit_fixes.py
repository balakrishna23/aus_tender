import io
import os
import sys
import tempfile
from pathlib import Path
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import scrapy
from scrapy.http import TextResponse

from database_manager import DatabaseManager
from export_to_gsheets import ensure_worksheet, write_df
from sheets_writer import replace_worksheets, write_literal_dataframe
from spiders.aus_contract import AusContractSpider
from spiders.buy_ict import BuyIctApiSpider
import run_spiders

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log


class ContractDataTests(unittest.TestCase):
    def test_currency_and_supplier_locations_survive_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            db = DatabaseManager(directory)
            db.initialize_database('aus_contract')
            self.assertTrue(db.insert_item('aus_contract', {
                'CN ID': 'CN1', 'Agency': 'Agency', 'URL': 'https://example.test/1',
                'Supplier Town/City': 'Sydney', 'Supplier State/Territory': 'NSW',
                'Contract Value (AUD)': '$1,234.00'}))
            with db.get_connection('aus_contract') as conn:
                row = conn.execute('SELECT supplier_town_city, supplier_state_territory, '
                                   'contract_value_aud, typeof(contract_value_aud), '
                                   'contract_value_raw FROM contracts').fetchone()
                self.assertEqual(tuple(row), ('Sydney', 'NSW', 1234.0, 'real', '$1,234.00'))
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM contracts WHERE '
                                              'contract_value_aud > 1000000').fetchone()[0], 0)

    def test_legacy_currency_migration_preserves_source_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            db = DatabaseManager(directory)
            with db.get_connection('aus_contract') as conn:
                conn.execute(db.SCHEMAS['aus_contract']['schema'].replace('contract_value_raw TEXT,', ''))
                conn.executemany('INSERT INTO contracts (url, contract_value_aud) VALUES (?, ?)',
                                 [('one', 'AUD 2,345.67'), ('two', 'Not disclosed')])
                conn.commit()
            for _ in range(2):
                db.initialize_database('aus_contract')
            with db.get_connection('aus_contract') as conn:
                rows = conn.execute('SELECT contract_value_aud, contract_value_raw FROM contracts ORDER BY id').fetchall()
                self.assertEqual([tuple(r) for r in rows],
                                 [(2345.67, 'AUD 2,345.67'), (None, 'Not disclosed')])

    def test_currency_edge_cases(self):
        for value, expected in [(0, 0.0), ('A$ 12,345.50', 12345.5),
                                ('$ (12.50)', -12.5), ('N/A', None),
                                ('1,23', None), ('NaN', None), ('', None)]:
            with self.subTest(value=value):
                self.assertEqual(DatabaseManager._parse_contract_value(value), expected)

    def test_contract_backfill_bypasses_persistent_duplicates(self):
        spider = AusContractSpider(refresh_existing='true')
        response = scrapy.http.HtmlResponse('https://example.test/list', encoding='utf-8', body=
            b'<div class="list-desc"><span>CN ID:</span><div class="list-desc-inner"><a href="/cn/1">1</a></div></div>')
        self.assertTrue(next(spider.parse(response)).meta['refresh_existing'])


class SheetsSafetyTests(unittest.TestCase):
    def target(self, sheet_id=1):
        return Mock(id=sheet_id, row_count=101, col_count=5)

    def test_append_preserves_existing_rows_and_disables_formulas(self):
        target = self.target()
        target.get_all_values.return_value = [['header']] + [[str(i)] for i in range(100)]
        spreadsheet = Mock()
        spreadsheet.worksheet.return_value = target
        with patch('sheets_writer.set_with_dataframe') as writer:
            ws = ensure_worksheet(spreadsheet, 'data', 3, 1)
            write_df(ws, pd.DataFrame({'text': ['=1+1', '0012']}), append=True)
        target.resize.assert_not_called()
        target.clear.assert_not_called()
        self.assertEqual(writer.call_args.kwargs['row'], 102)
        self.assertFalse(writer.call_args.kwargs['include_column_header'])
        self.assertFalse(writer.call_args.kwargs['allow_formulas'])
        self.assertEqual(writer.call_args.kwargs['string_escaping'], 'full')

    def test_literal_cells_using_real_dataframe_encoder(self):
        target = self.target()
        write_literal_dataframe(target, pd.DataFrame({'text': ['=1+1', '0012']}))
        cells = target.update_cells.call_args.args[0]
        self.assertEqual([c.value for c in cells], ["'text", "'=1+1", "'0012"])

    def test_failed_second_chunk_leaves_live_sheet_untouched(self):
        target, stage, spreadsheet = self.target(), self.target(2), Mock()
        spreadsheet.add_worksheet.return_value = stage
        with patch('sheets_writer.write_literal_dataframe', side_effect=[None, RuntimeError('upload failed')]):
            with self.assertRaisesRegex(RuntimeError, 'upload failed'):
                replace_worksheets(spreadsheet, [(target, pd.DataFrame({'x': [1, 2]}))], chunk_size=1)
        target.resize.assert_not_called()
        target.clear.assert_not_called()
        spreadsheet.batch_update.assert_not_called()
        spreadsheet.del_worksheet.assert_called_once_with(stage)

    def test_all_sheets_staged_before_single_publish(self):
        spreadsheet = Mock()
        targets = [self.target(1), self.target(2)]
        stages = [self.target(3), self.target(4)]
        spreadsheet.add_worksheet.side_effect = stages
        with patch('sheets_writer.write_literal_dataframe') as writer:
            def publish(body):
                self.assertEqual(writer.call_count, 2)
                copies = [r['copyPaste'] for r in body['requests'] if 'copyPaste' in r]
                self.assertEqual([c['destination']['sheetId'] for c in copies], [1, 2])
                self.assertTrue(all(c['pasteType'] == 'PASTE_VALUES' for c in copies))
            spreadsheet.batch_update.side_effect = publish
            replace_worksheets(spreadsheet, [(t, pd.DataFrame({'x': [1]})) for t in targets])
        spreadsheet.batch_update.assert_called_once()
        self.assertEqual(spreadsheet.del_worksheet.call_count, 2)
        for target in targets:
            target.resize.assert_not_called()
            target.clear.assert_not_called()

    def test_failed_second_sheet_does_not_publish_first(self):
        spreadsheet = Mock()
        spreadsheet.add_worksheet.side_effect = [self.target(3), self.target(4)]
        with patch('sheets_writer.write_literal_dataframe', side_effect=[None, RuntimeError('upload failed')]):
            with self.assertRaises(RuntimeError):
                replace_worksheets(spreadsheet, [(self.target(i), pd.DataFrame({'x': [1]})) for i in (1, 2)])
        spreadsheet.batch_update.assert_not_called()

    def test_publish_failure_is_propagated_and_staging_cleaned(self):
        spreadsheet = Mock()
        stage = self.target(2)
        spreadsheet.add_worksheet.return_value = stage
        spreadsheet.batch_update.side_effect = RuntimeError('publish rejected')
        with patch('sheets_writer.write_literal_dataframe'):
            with self.assertRaisesRegex(RuntimeError, 'publish rejected'):
                replace_worksheets(spreadsheet, [(self.target(), pd.DataFrame({'x': [1]}))])
        spreadsheet.del_worksheet.assert_called_once_with(stage)

    def test_empty_frame_publishes_header(self):
        spreadsheet = Mock()
        stage = self.target(2)
        spreadsheet.add_worksheet.return_value = stage
        with patch('sheets_writer.write_literal_dataframe') as writer:
            replace_worksheets(spreadsheet, [(self.target(), pd.DataFrame(columns=['x']))])
        writer.assert_called_once()
        self.assertTrue(writer.call_args.kwargs['include_column_header'])
        spreadsheet.batch_update.assert_called_once()


class CrawlFailureTests(unittest.TestCase):
    def test_empty_listing_after_other_success_is_recorded(self):
        spider = AusContractSpider()
        spider.crawler = SimpleNamespace(stats=Mock())
        response = scrapy.http.HtmlResponse('https://example.test/list', body=b'<html>Unexpected page</html>')
        with self.assertLogs('aus_contract', level='ERROR'):
            self.assertEqual(list(spider.parse(response)), [])
        spider.crawler.stats.inc_value.assert_called_with('crawl/extraction_failed')

    def test_runner_rejects_partial_success_stats(self):
        base = "'downloader/response_count': 3, 'finish_reason': 'finished', 'item_scraped_count': 1, 'database/items_saved': 1"
        for stat in ['httperror/response_ignored_count', 'retry/max_reached',
                     'crawl/extraction_failed', 'spider_exceptions/ValueError']:
            with self.subTest(stat=stat), tempfile.TemporaryDirectory() as logs, patch.object(run_spiders, 'LOG_DIR', Path(logs)), patch.object(sys, 'argv', ['run_spiders.py', '--spiders', 'aus_tender', '--no-sync', '--no-feed']), patch.object(run_spiders, 'load_dotenv'), patch.object(run_spiders, 'DatabaseManager'), patch.object(run_spiders, 'rotate_log'), patch.object(run_spiders, 'collect_db_count', return_value=1), patch.object(run_spiders, 'run_spider', return_value=0), patch.object(run_spiders, 'read_text', return_value=base + f", '{stat}': 1"), patch.dict(os.environ), redirect_stdout(io.StringIO()):
                self.assertEqual(run_spiders.main(), 1)

    def test_buyict_missing_details_are_failures(self):
        with patch('spiders.buy_ict.DatabaseManager'):
            spider = BuyIctApiSpider()
        spider.crawler = SimpleNamespace(stats=Mock())
        request = scrapy.Request('https://example.test/api', meta={'fallback_attempted': True})
        response = TextResponse(request.url, request=request, body=b'{}', encoding='utf-8')
        with self.assertLogs('buy_ict', level='ERROR'):
            self.assertEqual(list(spider.parse_detail_rectangle(response)), [])
        spider.crawler.stats.inc_value.assert_called_with('crawl/extraction_failed')

    def test_buyict_missing_listing_payload_is_failure(self):
        with patch('spiders.buy_ict.DatabaseManager'):
            spider = BuyIctApiSpider()
        spider.crawler = SimpleNamespace(stats=Mock())
        request = scrapy.Request('https://example.test/api', meta={'token': 'test', 'page_num': 2})
        response = TextResponse(request.url, request=request, body=b'{}', encoding='utf-8')
        with self.assertLogs('buy_ict', level='ERROR'):
            self.assertEqual(list(spider.parse_results_rectangle(response)), [])
        spider.crawler.stats.inc_value.assert_called_with('crawl/extraction_failed')


class SheetSizeTests(unittest.TestCase):
    def test_smaller_refresh_shrinks_grid_and_pastes_once(self):
        spreadsheet = Mock()
        spreadsheet.add_worksheet.return_value = Mock(id=9, row_count=3, col_count=2)
        target = Mock(id=1, row_count=10549, col_count=21)  # sized for an older, bigger refresh
        with patch('sheets_writer.write_literal_dataframe'):
            replace_worksheets(spreadsheet, [(target, pd.DataFrame({'x': [1, 2], 'y': [3, 4]}))])
        reqs = spreadsheet.batch_update.call_args.args[0]['requests']
        grid = next(r for r in reqs if 'updateSheetProperties' in r)['updateSheetProperties']['properties']
        self.assertEqual(grid['gridProperties'], {'rowCount': 3, 'columnCount': 2})
        dest = next(r for r in reqs if 'copyPaste' in r)['copyPaste']['destination']
        self.assertEqual((dest['endRowIndex'], dest['endColumnIndex']), (3, 2))  # never tiled
