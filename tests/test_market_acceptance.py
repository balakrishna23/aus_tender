"""Phase sign-off report: honest statuses and safe output.

The rendering test runs everywhere; the measurement test needs the disposable PostgreSQL test database."""
from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest

from market_intelligence.acceptance import _criterion, write_acceptance


class AcceptanceRenderingTests(unittest.TestCase):
    def test_html_escapes_source_text_and_keeps_statuses(self):
        report = dict(as_of=date(2026, 11, 27), status='measured', summary={'2': {'owner_review': 1}},
                      criteria=[_criterion('2', 'Top 25 <agencies>', 'owner_review',
                                           rows=[dict(buyer='<script>alert(1)</script>', value_12m=1)])])
        with tempfile.TemporaryDirectory() as tmp:
            out = write_acceptance(report, tmp)
            page = Path(out['html']).read_text(encoding='utf-8')
            data = json.loads(Path(out['json']).read_text(encoding='utf-8'))
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        self.assertIn('Needs your review', page)
        self.assertEqual(data['criteria'][0]['status'], 'owner_review')

    def test_rows_are_capped(self):
        self.assertEqual(len(_criterion('1', 'x', 'owner_review', rows=[{'a': i} for i in range(40)])['rows']), 25)


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'), 'Disposable PostgreSQL test database not configured')
class AcceptanceDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine, migrate
        cls.engine = engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database != 'illoura_market_test':
            raise RuntimeError('Integration tests require the dedicated illoura_market_test database')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def test_measures_without_claiming_unmeasured_criteria(self):
        from market_intelligence.acceptance import build_acceptance
        report = build_acceptance(self.engine, date(2026, 11, 27))
        statuses = {c['status'] for c in report['criteria']}
        self.assertTrue(statuses <= {'met', 'not_met', 'owner_review', 'not_measurable_here'})
        by_spec = {c['spec']: c for c in report['criteria']}
        self.assertEqual(by_spec['Tests pass']['status'], 'not_measurable_here')
        self.assertEqual(by_spec['Extraction is spot-checked on 30 RAPs with at least 90% field accuracy']['status'], 'not_met')
        for item in report['criteria']:
            if item['spec'].endswith('look plausible to me') or item['spec'].endswith('reviewed by me'):
                self.assertNotEqual(item['status'], 'met')


if __name__ == '__main__':
    unittest.main()
