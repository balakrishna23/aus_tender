"""Part B: result classification must not certify missing persistence or no sources."""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
import run_spiders as r


class RunnerClassification(unittest.TestCase):
    def batch(self, log, spiders=None):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            report = Path(tmp) / 'report.json'
            args = SimpleNamespace(report=report, spiders=spiders if spiders is not None else ['aus_contract'],
                                   backfill_since=None, timeout=1, retries=0, fail_fast=False, no_feed=True)
            for name in ['DatabaseManager', 'ping_healthcheck', 'rotate_log', 'archive_logs']:
                stack.enter_context(patch.object(r, name))
            stack.enter_context(patch.object(r, 'collect_db_count', return_value=2))
            stack.enter_context(patch.object(r, 'run_spider', return_value=0))
            stack.enter_context(patch.object(r, 'read_text', return_value=log))
            stack.enter_context(patch.object(r, 'data_quality_warnings', return_value=[]))
            stack.enter_context(patch.object(r, 'LOG_DIR', Path(tmp)))
            for name in ['start_run', 'record_scrape', 'finish_run']:
                stack.enter_context(patch('activity_log.' + name))
            stack.enter_context(patch.dict(sys.modules, {'dashboard': Mock()}))
            stack.enter_context(patch.dict(os.environ))
            stack.enter_context(redirect_stdout(io.StringIO()))
            code = r.run_batch(args, False)
            return code, json.loads(report.read_text()) if report.exists() else None

    def test_scraped_but_nothing_persisted_is_failure(self):
        log = "{'downloader/response_count': 3, 'item_scraped_count': 3, 'database/items_saved': 0, 'finish_reason': 'finished'}"
        code, report = self.batch(log)
        self.assertEqual(code, 1)
        self.assertFalse(report['success'])

    def test_no_requested_sources_is_not_success(self):
        code, _ = self.batch('', [])
        self.assertEqual(code, 1)

    def test_healthy_persisted_collection_succeeds(self):
        code, report = self.batch("{'downloader/response_count': 3, 'item_scraped_count': 3, 'database/items_saved': 3, 'finish_reason': 'finished'}")
        self.assertEqual(code, 0)
        self.assertTrue(report['success'])

    def test_later_sync_failure_overrides_earlier_success(self):
        _, _, status = r.parse_log_for_metrics('aus_contract',
            'Synced 3 rows to worksheet\nGoogle Sheets auto-sync failed aus_contract: unavailable', True)
        self.assertEqual(status, 'failed')
