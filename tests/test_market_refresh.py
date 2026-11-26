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

    def test_readiness_counts_and_coverage_match_full_analytical_queries(self):
        from datetime import timedelta
        from market_intelligence.readiness import readiness
        from market_intelligence.procurement import import_procurement
        from test_market_intelligence import procurement_export
        from sqlalchemy import text
        as_of=date(2026,11,19)
        with tempfile.TemporaryDirectory() as tmp:
            base=dict(buyer='Synthetic Readiness Buyer',buyer_abn=None,
                      supplier='Synthetic Supplier',supplier_abn='51824753556',category_family='ICT services',
                      current_value=120,start_date='2026-01-01',current_end_date='2027-02-01',
                      first_published='2026-01-01',page_url='https://example.invalid/contract',
                      retrieved_at='2026-11-19T00:00:00Z')
            rows=[dict(base,cn_id='CN80001',original_value=100),
                  dict(base,cn_id='CN80002',original_value=200),
                  dict(base,cn_id='CN80003',buyer='Second Synthetic Buyer',original_value=300)]
            import_procurement(self.engine,procurement_export(Path(tmp),rows))
        result=readiness(self.engine,as_of)
        self.assertEqual(result['recent_original_award_value'],600)
        with self.engine.connect() as conn:
            for name,count in result['analytical_view_rows'].items():
                self.assertEqual(count,conn.execute(text('SELECT count(*) FROM market.'+name)).scalar(),name)
            coverage=conn.execute(text('''SELECT sum(c.original_value) AS value,
                sum(c.original_value) FILTER(WHERE r.confirmed_organisation_id IS NOT NULL AND r.review_status='approved'
                    AND NOT r.register_context_stale) AS reviewed_value,
                sum(c.original_value) FILTER(WHERE r.organisation_id IS NOT NULL AND r.review_status NOT IN ('rejected','needs_recheck')
                    AND NOT r.register_context_stale) AS proposed_value
                FROM market.current_analytical_contracts c LEFT JOIN market.buyer_resolution r USING(buyer_key)
                WHERE c.first_published>:past AND c.first_published<=:asof'''),
                {'asof':as_of,'past':as_of-timedelta(days=365)}).mappings().one()
        self.assertEqual(result['recent_original_award_value'],coverage['value'])
        for name,column in [('reviewed_buyer_value_coverage','reviewed_value'),
                            ('proposed_buyer_value_coverage','proposed_value')]:
            expected=float((coverage[column] or 0)/coverage['value']) if coverage['value'] else None
            self.assertEqual(result[name],expected)


if __name__=='__main__':unittest.main()
