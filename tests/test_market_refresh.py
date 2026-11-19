from datetime import date
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL database required')
class MarketRefreshDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.engine=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose()

    def test_failed_import_records_failure_without_publishable_package(self):
        from market_intelligence.refresh import refresh
        from sqlalchemy import text
        with tempfile.TemporaryDirectory() as tmp:
            output=Path(tmp)/'run'
            with patch('market_intelligence.refresh.import_procurement',side_effect=ValueError('fixture')):
                result=refresh(self.engine,'unused','unused',output,date(2026,11,19))
            self.assertEqual(result['status'],'failed');self.assertNotIn('package',result)
            self.assertFalse((output/'digest/market_brief.json').exists())
            self.assertEqual(json.loads((output/'refresh_report.json').read_text())['error_code'],'ValueError')
            with self.engine.connect() as conn:
                status=conn.execute(text('SELECT status FROM market.pipeline_runs WHERE id=:id'),{'id':result['run_id']}).scalar()
            self.assertEqual(status,'failed')


if __name__=='__main__':unittest.main()
