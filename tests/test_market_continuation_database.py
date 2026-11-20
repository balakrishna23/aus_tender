from contextlib import closing
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

RAP_REVIEWER='00000000-0000-4000-8000-000000000001'


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL test database not configured')
class ContinuationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.engine=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.engine.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.engine)

    @classmethod
    def tearDownClass(cls):cls.engine.dispose()

    def setUp(self):
        from sqlalchemy import text
        with self.engine.begin() as conn:
            conn.execute(text('TRUNCATE market.current_research_snapshots,market.research_records,market.research_snapshots'))
            conn.execute(text('TRUNCATE market.current_forward_snapshot,market.forward_observations,market.forward_snapshots'))
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def bundle(self,source,records,cleared=False):
        path=self.root/(source+'.json');provenance={'licence':'Test fixture; no real organisation'}
        data=dict(source=source,observed_at='2026-11-19T12:00:00+00:00',contains_contact_data=False,provenance=provenance,records=records)
        if cleared:
            # RAP exports need an accountable human privacy clearance bound to the exact records.
            from market_intelligence.rap_extract import digest
            data['privacy_review']=dict(reviewer_id=RAP_REVIEWER,contains_personal_data=False,
                                        payload_sha256=digest({'records':records,'provenance':provenance}))
        path.write_text(json.dumps(data))
        return path

    def test_rap_same_document_evidence_and_idempotency(self):
        from unittest.mock import patch
        from market_intelligence.parsers import SourceError
        from market_intelligence.research import import_research,build_rap
        sha='a'*64
        base=dict(organisation='Fictional Research Company',rap_type='Innovate',period_as_published='April 2026 to April 2028',
                  document_url='https://example.invalid/rap.pdf',source_url='https://example.invalid/directory',document_sha256=sha,
                  collection_date='2026-10-27',licence='Test fixture')
        rows=[dict(base,record_key='current'),dict(base,record_key='expired',period_as_published='April 2020 to April 2022',document_sha256='b'*64)]
        finding=dict(record_key='finding',organisation=base['organisation'],reviewed_on='2026-11-05',action='Procurement',pdf_page=4,
                     summary='Review Indigenous procurement opportunities',timeline='2026',source_url=base['document_url'],
                     period_start='2026-04-01',period_end='2028-04-30',candidate_abns=[],document_sha256=sha,review_method='Test fixture',
                     entity_scope='unconfirmed',target_completion='unknown')
        with patch.dict(os.environ,{'ILLOURA_RAP_REVIEWER_IDS':RAP_REVIEWER}):
            with self.assertRaises(SourceError):import_research(self.engine,self.bundle('rap_directory',rows))
            directory=self.bundle('rap_directory',rows,cleared=True)
            self.assertEqual(import_research(self.engine,directory)['status'],'complete')
            self.assertEqual(import_research(self.engine,directory)['status'],'unchanged')
            import_research(self.engine,self.bundle('rap_commitments',[finding],cleared=True))
            report=build_rap(self.engine,date(2026,11,19))
        targets={r['record_key']:r for r in report['targets']}
        current,expired=targets['current'],targets['expired']
        # Legacy manual commitments have no exact-document approval event, so they stay out of reports.
        self.assertEqual((current['commitment_count'],expired['commitment_count'],expired['score']),(0,0,0))
        self.assertEqual((current['period_status'],expired['period_status']),('within_published_period','expired'))
        self.assertEqual(current['delivery_shortfall'],'unknown')

    def test_forward_missing_title_retained_and_reimport_unchanged(self):
        from market_intelligence.extract_forward import FIELDS,export_forward
        from market_intelligence.forward import import_forward,build_forward
        source=self.root/'feed.db'
        with closing(sqlite3.connect(source)) as conn,conn:
            conn.execute('CREATE TABLE opportunities('+','.join(k+' TEXT' for k in FIELDS)+')')
            row=dict(source='AusTender plan',source_id='PLAN1',kind='Planned procurement',title=None,buyer='Fictional Agency',
                     category='ICT services',unspsc='81111500',expected_from='2027-01-01',expected_to='2027-03-31',
                     url='https://example.invalid/plan',retrieved_at='2026-11-19T00:00:00')
            conn.execute('INSERT INTO opportunities VALUES('+','.join('?' for _ in FIELDS)+')',[row.get(k) for k in FIELDS])
            second=dict(row,buyer='Another Fictional Agency',url='https://example.invalid/another-plan')
            conn.execute('INSERT INTO opportunities VALUES('+','.join('?' for _ in FIELDS)+')',[second.get(k) for k in FIELDS])
        export=self.root/'export.db';export_forward(source,export)
        self.assertEqual(import_forward(self.engine,export)['status'],'complete')
        self.assertEqual(import_forward(self.engine,export)['status'],'unchanged')
        report=build_forward(self.engine,date(2026,11,19))
        plan=next(r for r in report['signals'] if r['source_id']=='PLAN1')
        self.assertEqual(plan['signal'],'published_plan');self.assertIn('missing_title',plan['payload']['issues'])
        self.assertEqual(sum(r['source_id']=='PLAN1' for r in report['signals']),2)

    def test_business_inputs_reimport_and_unknown_bench_mapping(self):
        from market_intelligence.inputs import import_input,build_bench
        path=self.root/'bench.csv';path.write_text('role_family,state,clearance,count,as_at\ncyber,ACT,NV1,3,2026-11-19\n')
        first=import_input(self.engine,'bench',path,'https://example.invalid/ats-export',date(2026,11,19),'Fixture reviewer')
        second=import_input(self.engine,'bench',path,'https://example.invalid/ats-export',date(2026,11,19),'Fixture reviewer')
        self.assertEqual((first['status'],second['status']),('complete','unchanged'))
        report=build_bench(self.engine,date(2026,11,19))
        self.assertEqual(report['bench_records'],1);self.assertEqual(report['rows'],[])

    def test_panel_evidence_rejects_old_verification_and_preserves_history(self):
        from sqlalchemy import text
        from market_intelligence.panel_access import record_panel_evidence
        with self.engine.begin() as conn:
            conn.execute(text("INSERT INTO market.panel_watchlist(son_id,panel_name,seeded_from) VALUES('SON-fixture-panel','Fictional Panel','test') ON CONFLICT DO NOTHING"))
        path=self.root/'panel.json';data=dict(son_id='SON-fixture-panel',facts={'current_expiry':'2027-05-31','requirements':[]},
            source_url='https://example.invalid/panel',verified_at='2026-11-19T00:00:00+00:00',content_sha256=hashlib.sha256(b'fixture').hexdigest())
        path.write_text(json.dumps(data));record_panel_evidence(self.engine,path,'Fixture reviewer')
        data['verified_at']='2026-11-18T00:00:00+00:00';path.write_text(json.dumps(data))
        with self.assertRaises(ValueError):record_panel_evidence(self.engine,path,'Fixture reviewer')
        with self.engine.connect() as conn:
            self.assertGreaterEqual(conn.execute(text("SELECT count(*) FROM market.panel_evidence_events WHERE son_id='SON-fixture-panel'")).scalar(),1)


if __name__=='__main__':unittest.main()
