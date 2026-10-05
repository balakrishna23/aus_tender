import asyncio
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import scrapy
from scrapy.exceptions import DropItem
from scrapy.http import HtmlResponse

from database_manager import DatabaseManager
from db_query_tool import DatabaseQueryTool
from duplicate_filter import DatabaseAwareDupeFilter
from middlewares import SpiderNameMiddleware
from pipelines import DatabasePipeline
from project_paths import PROJECT_DIR
from run_spiders import parse_log_for_metrics, stream_command
import run_spiders
from spiders.aus_tender import AusTenderSpider

os.environ.setdefault('ACTIVITY_DB', os.path.join(tempfile.mkdtemp(), 'activity.db'))  # not the real audit log


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = DatabaseManager(self.temp.name)

    def test_all_schemas_and_upserts(self):
        for name, schema in self.db.SCHEMAS.items():
            with self.subTest(name=name):
                self.db.initialize_database(name)
                self.assertTrue(self.db.insert_item(name, {'url': 'https://example.test/1'}))
                self.assertTrue(self.db.insert_item(name, {'url': 'https://example.test/1'}))
                self.assertEqual(self.db.get_stats(name)['total_records'], 1)

    def test_identity_update_keeps_row(self):
        self.db.initialize_database('aus_tender')
        self.db.insert_item('aus_tender', {'ATM ID': 'A1', 'Agency': 'Original', 'URL': 'https://example.test/old'})
        self.db.insert_item('aus_tender', {'ATM ID': 'A1', 'Agency': 'Updated', 'URL': 'https://example.test/new'})
        with self.db.get_connection('aus_tender') as conn:
            rows = conn.execute('SELECT id, agency, url FROM tenders').fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(tuple(rows[0]), (1, 'Updated', 'https://example.test/new'))

    def test_buyict_legacy_migration(self):
        with self.db.get_connection('buy_ict') as conn:
            conn.execute(self.db.SCHEMAS['buy_ict']['schema'].replace('title TEXT,', ''))
            conn.commit()
        self.db.initialize_database('buy_ict')
        self.assertTrue(self.db.insert_item('buy_ict', {'url': 'https://example.test/1', 'title': 'New title'}))

    def test_buyict_summary(self):
        self.db.initialize_database('buy_ict')
        self.db.insert_item('buy_ict', {'url': 'https://example.test/1', 'buyer': 'Agency'})
        stats = DatabaseQueryTool(self.temp.name).get_summary_stats('buy_ict')
        self.assertEqual(stats['top_agencies'], [{'agency': 'Agency', 'count': 1}])

    def test_path_override_and_unknown_spider(self):
        with patch.dict(os.environ, {'AUSTENDER_DB_DIR': self.temp.name}):
            self.assertEqual(DatabaseManager().db_directory, Path(self.temp.name))
        with self.assertRaises(ValueError):
            self.db.get_db_path('../unexpected')

    def test_pipeline_rejects_invalid_and_failed_writes(self):
        with patch('pipelines.DatabaseManager', return_value=self.db):
            pipeline = DatabasePipeline()
        spider = SimpleNamespace(name='aus_tender', crawler=SimpleNamespace(stats=Mock()))
        pipeline.open_spider(spider)
        with self.assertRaises(DropItem):
            pipeline.process_item({'Agency': 'A'}, spider)
        with patch.object(self.db, 'insert_item', return_value=False):
            with self.assertRaises(DropItem):
                pipeline.process_item({'ATM ID': '1', 'Agency': 'A', 'URL': 'https://example.test/1'}, spider)
        self.assertEqual(pipeline.stats['aus_tender']['items_failed'], 2)
        self.assertEqual(pipeline.stats['aus_tender']['items_saved'], 0)

    def test_pipeline_accepts_normalized_fields(self):
        with patch('pipelines.DatabaseManager', return_value=self.db):
            pipeline = DatabasePipeline()
        spider = SimpleNamespace(name='aus_tender', crawler=SimpleNamespace(stats=Mock()))
        pipeline.open_spider(spider)
        pipeline.process_item({'atm_id': '1', 'agency': 'A', 'url': 'https://example.test/1'}, spider)
        self.assertEqual(self.db.get_stats('aus_tender')['total_records'], 1)


class RequestTests(unittest.TestCase):
    def test_empty_request_metadata(self):
        request = scrapy.Request('https://example.test')
        result = list(SpiderNameMiddleware().process_spider_output(None, [request], SimpleNamespace(name='test')))
        self.assertEqual(result[0].meta['spider_name'], 'test')

    def test_async_request_metadata(self):
        async def run():
            async def source():
                yield scrapy.Request('https://example.test')
            return [r async for r in SpiderNameMiddleware().process_spider_output_async(None, source(), SimpleNamespace(name='test'))]
        self.assertEqual(asyncio.run(run())[0].meta['spider_name'], 'test')

    def test_mutable_notices_refresh(self):
        with patch('duplicate_filter.DatabaseManager') as manager:
            manager.return_value.url_exists.return_value = True
            dupe = DatabaseAwareDupeFilter()
            request = scrapy.Request('https://example.test/detail')
            self.assertFalse(dupe._is_database_duplicate(request, 'act_tender'))
            self.assertTrue(dupe._is_database_duplicate(request, 'aus_contract'))

    def test_tender_keeps_time_of_day(self):
        body = b'<div class="list-desc"><span><label>Close Date &amp; Time</label></span><div class="list-desc-inner">30 Sep 2026 5:00 pm (ACT Local Time)</div></div>'
        response = HtmlResponse('https://example.test/tender', body=body, encoding='utf-8')
        item = next(AusTenderSpider().parse_tender(response))
        self.assertIn('5:00 pm', item['Close Date & Time'])


class RunnerTests(unittest.TestCase):
    def test_batch_failure_status_and_report(self):
        cases = [
            ("'downloader/response_count': 1, 'finish_reason': 'finished', 'item_scraped_count': 1, 'database/items_saved': 1", 0),
            ("'downloader/response_count': 1, 'finish_reason': 'finished'", 1),
            ("'downloader/response_count': 1, 'finish_reason': 'closespider_timeout', 'item_scraped_count': 1", 1),
            ("'downloader/response_count': 1, 'finish_reason': 'finished', 'database/items_failed': 1", 1),
        ]
        import json
        import io
        from contextlib import redirect_stdout
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / 'report.json'
            for log, expected in cases:
                with self.subTest(log=log), patch.object(run_spiders, 'LOG_DIR', Path(directory)), patch.object(sys, 'argv', ['run_spiders.py', '--spiders', 'aus_tender', '--no-sync', '--no-feed', '--report', str(report)]), patch.object(run_spiders, 'load_dotenv'), patch.object(run_spiders, 'DatabaseManager'), patch.object(run_spiders, 'rotate_log'), patch.object(run_spiders, 'collect_db_count', return_value=10), patch.object(run_spiders, 'run_spider', return_value=0), patch.object(run_spiders, 'read_text', return_value=log), patch.dict(os.environ), redirect_stdout(io.StringIO()):
                    self.assertEqual(run_spiders.main(), expected)
                    self.assertEqual(json.loads(report.read_text())['success'], expected == 0)

    def test_metrics_do_not_count_stats_as_errors(self):
        log = "2026 [test] ERROR: broken\n'log_count/ERROR': 1\n"
        self.assertEqual(parse_log_for_metrics('test', log, False), (0, 1, 'not_enabled'))

    def test_silent_child_timeout(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            stream_command([sys.executable, '-c', 'import time; time.sleep(30)'], 1)

    def test_successful_child(self):
        self.assertEqual(stream_command([sys.executable, '-c', 'pass'], 5), 0)

    def test_child_that_closes_output_still_times_out(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            stream_command([sys.executable, '-c', 'import os,time; os.close(1); os.close(2); time.sleep(30)'], 1)

    def test_invalid_cli_arguments(self):
        for args in (['--spiders', 'typo'], ['--retries', '-1'], ['--spiders']):
            result = subprocess.run([sys.executable, str(PROJECT_DIR / 'run_spiders.py'), *args], capture_output=True)
            self.assertEqual(result.returncode, 2)


if __name__ == '__main__':
    unittest.main()
