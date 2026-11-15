"""Coordinator must validate report contents even when success is claimed."""
import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('report_coordinator',Path(__file__).resolve().parents[1]/'routine/run_once.py')
coordinator=importlib.util.module_from_spec(spec)
spec.loader.exec_module(coordinator)

class ReportIntegrity(unittest.TestCase):
    def test_success_does_not_override_missing_sources_or_feed_failure(self):
        rows=[dict(spider=name,status='OK') for name in coordinator.SOURCES]
        reports=[dict(success=True,feed='rebuilt',results=[dict(spider='buy_ict',status='OK')]*8),
                 dict(success=True,feed='FAILED',results=rows),
                 dict(success=True,feed=True,results=rows),
                 dict(success=True,feed='',results=rows),
                 dict(success=True,feed='rebuilt',results=[1]*8),
                 dict(success=True,feed='rebuilt',results=[dict(row,crawl_failed=True) for row in rows])]
        for report in reports:
            with self.subTest(report=report):
                self.assertEqual(coordinator.source_gate(report,0),(False,[]))
