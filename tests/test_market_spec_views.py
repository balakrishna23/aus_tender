"""Offline score and shaping tests, plus opt-in isolated PostgreSQL publication tests."""
from copy import deepcopy
from datetime import date
import importlib.util
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from market_intelligence.account_score import load_config, score_accounts, validate_config
from market_intelligence.views import VIEW_NAMES, SnapshotEngine, shape_row, invitation_matches, lookalikes, refresh_views


def account(key='one',lane='federal',**values):
    return dict(account_key=key,account=key,lane=lane,inputs=values,target_roles=['Procurement lead'])


class ScoreTests(unittest.TestCase):
    def test_config_validates_all_lanes(self):
        config=load_config()
        for weights in config['lanes'].values():self.assertAlmostEqual(sum(weights.values()),1)

    def test_config_rejects_invalid_weights_and_schema(self):
        for invalid in (float('nan'),float('inf'),-1,True,'1',.9):
            config=deepcopy(load_config());config['lanes']['federal']['spend']=invalid
            with self.assertRaises(ValueError):validate_config(config)
        config=deepcopy(load_config());config['penalties']['extra']=.1
        with self.assertRaises(ValueError):validate_config(config)
        config=deepcopy(load_config());config['lanes']['federal']['nonexistent']=0
        with self.assertRaises(ValueError):validate_config(config)

    def test_nulls_keep_weights_without_rescaling(self):
        row=score_accounts([account(spend=100)],load_config())[0]
        self.assertEqual(row['score'],10)
        self.assertEqual(row['score_coverage_pct'],20)
        self.assertIsNone(row['components']['budget']['value'])
        self.assertIsNone(row['components']['budget']['points'])
        self.assertIn('Unknown',row['components']['budget']['basis'])

    def test_all_unknown_score_is_unknown(self):
        row=score_accounts([account()],load_config())[0]
        self.assertIsNone(row['score']);self.assertEqual(row['score_coverage_pct'],0)

    def test_zero_is_known(self):
        row=score_accounts([account(spend=0)],load_config())[0]
        self.assertEqual(row['score'],0);self.assertEqual(row['score_coverage_pct'],20)

    def test_per_lane_ranks_and_overall_rank(self):
        rows=score_accounts([account('low',spend=10),account('high',spend=100),account('private','private',supplier_value=100)],load_config())
        by_key={r['account_key']:r for r in rows}
        self.assertEqual(by_key['high']['rank_in_lane'],1)
        self.assertEqual(by_key['low']['rank_in_lane'],2)
        self.assertEqual(by_key['private']['rank_in_lane'],1)
        self.assertEqual([r['overall_rank'] for r in rows],[1,2,3])
        self.assertEqual(by_key['high']['score'],15)

    def test_unknown_and_applied_penalties(self):
        row=account(spend=100,frequency=10)
        unknown=score_accounts([row],load_config())[0]
        self.assertIsNone(unknown['penalties']['blocker']['points'])
        blocked=score_accounts([dict(row,penalty_inputs=dict(blocker=1,insourcing=None))],load_config())[0]
        self.assertEqual(blocked['score'],0)
        self.assertEqual(blocked['penalties']['blocker']['points'],-20)
        self.assertEqual(blocked['score_coverage_pct'],unknown['score_coverage_pct'])

    def test_engaged_and_do_not_pursue_are_not_new(self):
        rows=score_accounts([dict(account('engaged'),already_engaged=True),dict(account('blocked'),relationship_status='do_not_pursue')],load_config())
        self.assertTrue(all(not r['new_target'] for r in rows))

    def test_invalid_input_rejected(self):
        for value in (-1,float('nan'),True):
            with self.assertRaises(ValueError):score_accounts([account(spend=value)],load_config())

    def test_accounts_without_a_name_sort_last_instead_of_failing(self):
        rows=score_accounts([dict(account('unnamed',spend=5),account=None),account('Beta',spend=5),account('alpha',spend=5)],load_config())
        self.assertEqual([r['account_key'] for r in rows],['alpha','Beta','unnamed'])
        self.assertEqual([r['overall_rank'] for r in rows],[1,2,3])


class ShapeTests(unittest.TestCase):
    def test_typed_row_inputs_and_privacy(self):
        row=shape_row(dict(buyer='Agency',buyer_key='key',target_roles='ICT',verified_by='HIDDEN',notes='HIDDEN',evidence=[dict(source_url='https://example.invalid',reviewer='HIDDEN')]))
        self.assertEqual(row['account'],'Agency');self.assertEqual(row['target_roles'],['ICT'])
        self.assertEqual(row['account_key'],'key');self.assertNotIn('HIDDEN',json.dumps(row))
        self.assertIn('why_now',row);self.assertEqual(row['lane'],'federal')

    def test_invitation_conservative_matching(self):
        buyers=[dict(buyer='Agency One',buyer_key='a'),dict(buyer='Agency Two',buyer_key='b')]
        found=invitation_matches(buyers,[dict(buyer_key='a',invitation_status='invited'),dict(buyer_key='Agency',invitation_status='invited')])
        self.assertTrue(found['a']['recorded_invitation']);self.assertFalse(found['b']['recorded_invitation'])
        self.assertIn('absence does not establish',found['b']['invitation_basis'])
        found=invitation_matches(buyers,[dict(buyer_key='AGENCY TWO',invitation_status='invited')])
        self.assertTrue(found['b']['recorded_invitation'])

    def test_ambiguous_label_does_not_match(self):
        buyers=[dict(buyer='Agency One',buyer_key='a'),dict(buyer='AGENCY ONE',buyer_key='b')]
        found=invitation_matches(buyers,[dict(buyer_key='Agency One',invitation_status='invited')])
        self.assertFalse(any(v['recorded_invitation'] for v in found.values()))

    def test_lookalike_missing_dimensions_not_rescaled(self):
        rows=[dict(account('client'),features=dict(type='agency')),dict(account('target'),features=dict(type='agency'))]
        match=lookalikes(rows,{'client'})[1]
        self.assertEqual(match['similarity'],25);self.assertEqual(match['similarity_coverage_pct'],25)
        self.assertEqual(match['matched_client_key'],'client')
        self.assertIsNone(lookalikes(rows,set())[1]['similarity'])

    def test_snapshot_context_does_not_commit_or_change_isolation(self):
        from unittest.mock import Mock
        conn=Mock()
        with SnapshotEngine(conn).connect().execution_options(isolation_level='REPEATABLE READ') as shared,shared.begin():shared.execute('SELECT 1')
        conn.execute.assert_called_once_with('SELECT 1')
        conn.commit.assert_not_called();conn.close.assert_not_called();conn.execution_options.assert_not_called()

    def test_build_pass_reuses_builders_and_preserves_missing_sources(self):
        from contextlib import ExitStack
        from market_intelligence import views
        class Result:
            def scalar(self):return False
            def mappings(self):return self
            def __iter__(self):return iter([])
        class Conn:
            def execute(self,*args,**kwargs):return Result()
        reports={
            'analytics.build_report':dict(buyers=[dict(buyer_key='buyer',buyer='Agency',awarded_value_365d=100,recent_awards_count=2)],warnings=[]),
            'buyict.build_buyict':dict(buyers=[]),
            'levers.build_levers':dict(exemption_16_by_buyer=[],set_aside_band_by_buyer=[],portfolio_results=[],mmr_primes=[dict(supplier_abn='11111111111',supplier='UNSAFE CONTRACT NAME',active_high_value_contracts=1,value=10000000)]),
            'targets.build_targets':dict(supplier_profiles=[dict(supplier_abn='11111111111',organisation='Example Corporation Pty Ltd',original_awarded_value=2000000)],healthcare_accounts=[],partner_candidates=[],indigenous_corporations=[]),
            'research.build_rap':dict(targets=[dict(record_key='rapone',organisation='RAP Org',period_status='within_published_period',period_end=date(2027,1,1),rap_type='Innovate',approved_extraction=dict(employment_target=dict(value=True),requires_supply_nation_certified=dict(value='yes')))]),
            'panel_access.build_panel_access':dict(panels=[]),
            'forward.build_forward':dict(signals=[],snapshots=[]),
            'inputs.build_bench':dict(rows=[]),
            'changes.build_changes':dict(changes=[],baseline_gaps=[]),
        }
        with ExitStack() as stack:
            mocks=[stack.enter_context(patch('market_intelligence.views.'+name,return_value=report)) for name,report in reports.items()]
            rows,metrics=views.build_view_rows(Conn(),date(2026,11,27))
        self.assertEqual(set(rows),set(VIEW_NAMES))
        row=next(r for r in rows['account_score'] if r['lane']=='federal')
        private=next(r for r in rows['account_score'] if r['account_key']=='rap:rapone')
        self.assertEqual(private['components']['employment_target']['value'],1)
        self.assertIsNone(private['penalties']['blocker']['value'])
        self.assertNotIn('UNSAFE CONTRACT NAME',str(rows))
        self.assertEqual(rows['prime_targets'][0]['organisation'],'Example Corporation Pty Ltd')
        self.assertIsNone(row['components']['forward']['value'])
        self.assertIsNone(row['components']['panel_access']['value'])
        self.assertEqual(row['components']['spend']['value'],100)
        self.assertTrue(all(m.call_count==1 for m in mocks))
        self.assertIn('v_grant_triggers: no grant input data',metrics['skipped'])

    def test_register_rows_without_a_name_do_not_break_the_build(self):
        from contextlib import ExitStack
        from market_intelligence import views
        registry=[dict(abn='22222222222',name=None,source='acnc',attributes={},entity_type=None,source_url='https://acnc.example'),
                  dict(abn='22222222222',name='Second Charity Ltd',source='acnc',attributes={},entity_type='Charity',source_url='https://acnc.example'),
                  dict(abn='33333333333',name=None,source='acnc',attributes={},entity_type=None,source_url='https://acnc.example')]
        class Result:
            def __init__(self,rows):self.rows=rows
            def scalar(self):return False
            def mappings(self):return self
            def __iter__(self):return iter(self.rows)
        class Conn:
            def execute(self,statement,*args,**kwargs):
                return Result(registry if 'current_register_entities' in str(statement) else [])
        reports={
            'analytics.build_report':dict(buyers=[],warnings=[]),
            'buyict.build_buyict':dict(buyers=[]),
            'levers.build_levers':dict(exemption_16_by_buyer=[],set_aside_band_by_buyer=[],portfolio_results=[],
                mmr_primes=[dict(supplier_abn='22222222222',supplier='CONTRACT LABEL',active_high_value_contracts=1,value=1),
                            dict(supplier_abn='33333333333',supplier='OTHER LABEL',active_high_value_contracts=1,value=1)]),
            'targets.build_targets':dict(supplier_profiles=[],healthcare_accounts=[],partner_candidates=[],indigenous_corporations=[]),
            'research.build_rap':dict(targets=[]),
            'panel_access.build_panel_access':dict(panels=[]),
            'forward.build_forward':dict(signals=[],snapshots=[]),
            'inputs.build_bench':dict(rows=[]),
            'changes.build_changes':dict(changes=[],baseline_gaps=[]),
        }
        with ExitStack() as stack:
            for name,report in reports.items():stack.enter_context(patch('market_intelligence.views.'+name,return_value=report))
            rows,_=views.build_view_rows(Conn(),date(2026,11,28))
        self.assertEqual([p['organisation'] for p in rows['prime_targets']],['Second Charity Ltd'])
        self.assertNotIn('CONTRACT LABEL',str(rows))

    def test_publication_cap_preserves_each_lane(self):
        from unittest.mock import Mock
        from market_intelligence.views import publish_generation
        rows={name:[] for name in VIEW_NAMES}
        rows['account_score']=[account(str(i),'healthcare') for i in range(5)]+[account(str(i),'federal') for i in range(5)]
        with patch('market_intelligence.views.ROW_LIMIT',8):
            result=publish_generation(Mock(),date(2026,11,27),rows,{},'a'*64)
        counts=result['metrics']['row_counts']['account_score']
        self.assertEqual(counts['stored'],4);self.assertEqual(counts['total'],10)
        self.assertEqual(counts['lanes']['federal']['stored'],2)
        self.assertEqual(counts['lanes']['healthcare']['stored'],2)

    def test_daily_view_failure_is_recorded_without_failing_sources(self):
        import tempfile
        from datetime import datetime,timezone
        from test_market_freshness import MemoryDB
        from market_intelligence.research_refresh import refresh
        db=MemoryDB()
        with tempfile.TemporaryDirectory() as tmp,patch('market_intelligence.views.refresh_views',side_effect=ValueError('synthetic')),patch('market_intelligence.freshness.report',return_value={}):
            result=refresh(db,tmp,sources=[],now=datetime(2026,11,27,tzinfo=timezone.utc))
        self.assertEqual(result['status'],'complete')
        self.assertEqual(result['views']['status'],'failed')
        self.assertEqual([e['phase'] for e in db.events if e['source']=='views'],['started','finished'])

    def test_export_allowlist_and_bounds(self):
        import ast
        from deploy.illoura_research_export import TABLES,INDEXES
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'api/app.py').read_text())
        allowed=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='ALLOWED' for t in n.targets))
        self.assertEqual(set(allowed['research']),set(TABLES)|{'export_info'})
        for name in (*VIEW_NAMES,'rate_benchmarks'):
            self.assertIn('LIMIT 10000',TABLES[name]);self.assertIn('market.v_'+name,TABLES[name])
        self.assertIn('lane',INDEXES['account_score'])


@unittest.skipUnless(os.getenv('ILLOURA_MARKET_TEST_DATABASE_URL'),'Disposable PostgreSQL test database not configured')
class GenerationDatabaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from market_intelligence.db import engine,migrate
        cls.db=engine(os.environ['ILLOURA_MARKET_TEST_DATABASE_URL'])
        if cls.db.url.database!='illoura_market_test':raise RuntimeError('Dedicated test database required')
        migrate(cls.db)

    @classmethod
    def tearDownClass(cls):cls.db.dispose()

    def test_atomic_publication_failure_and_retention(self):
        from sqlalchemy import text
        def rows(value):
            result={n:[] for n in VIEW_NAMES}
            result['account_score']=[dict(account(value),score=20,rank_in_lane=1,overall_rank=1)]
            return result,{}
        with patch('market_intelligence.views.build_view_rows',return_value=rows('before')):
            before=refresh_views(self.db,date(2026,11,27))
        with patch('market_intelligence.views.build_view_rows',side_effect=RuntimeError('failed build')):
            with self.assertRaises(RuntimeError):refresh_views(self.db,date(2026,11,27))
        with self.db.connect() as c:
            self.assertEqual(str(c.execute(text('SELECT generation_id FROM market.current_view_generation')).scalar()),before['generation_id'])
            self.assertEqual(c.execute(text('SELECT account FROM market.v_account_score')).scalar(),'before')
        # Failure after inserts exercises the transaction rollback, not only build failure.
        from market_intelligence.views import publish_generation
        def fail(*args,**kwargs):
            publish_generation(*args,**kwargs)
            raise RuntimeError('failed after pointer switch')
        with patch('market_intelligence.views.build_view_rows',return_value=rows('after')),patch('market_intelligence.views.publish_generation',side_effect=fail):
            with self.assertRaises(RuntimeError):refresh_views(self.db,date(2026,11,27))
        with self.db.connect() as c:self.assertEqual(str(c.execute(text('SELECT generation_id FROM market.current_view_generation')).scalar()),before['generation_id'])
        with patch('market_intelligence.views.build_view_rows',return_value=rows('new')):
            for _ in range(31):last=refresh_views(self.db,date(2026,11,27))
        with self.db.connect() as c:
            self.assertEqual(c.execute(text('SELECT count(DISTINCT generation_id) FROM market.view_rows')).scalar(),30)
            self.assertEqual(str(c.execute(text('SELECT generation_id FROM market.current_view_generation')).scalar()),last['generation_id'])
            self.assertIsNotNone(c.execute(text('SELECT id FROM market.view_generations WHERE id=:id'),dict(id=before['generation_id'])).scalar())

    def test_rate_benchmark_fixtures(self):
        from sqlalchemy import text
        path=Path(__file__).resolve().parents[1]/'market_intelligence/migrations/versions/025_spec_views.py'
        spec=importlib.util.spec_from_file_location('spec_views_migration',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with self.db.connect() as c,c.begin():
            c.execute(text('CREATE TEMP TABLE rate_contracts(buyer_key text,buyer text,payload jsonb,start_date date,current_end_date date,current_value numeric,source_url text) ON COMMIT DROP'))
            fixtures=[('valid','2026-01-01','2026-12-31',365000,'single contractor'),('valid2','2026-01-01','2026-12-31',730000,'one resource'),('missing',None,'2026-12-31',365000,'single contractor'),('placeholder','1900-01-01','1900-12-31',365000,'single contractor'),('team','2026-01-01','2026-12-31',365000,'single contractor team'),('noheadcount','2026-01-01','2026-12-31',365000,'ICT services')]
            for key,start,end,value,title in fixtures:
                c.execute(text('INSERT INTO rate_contracts VALUES(:buyer,:buyer,CAST(:payload AS jsonb),:start,:end,:value,:url)'),dict(buyer='Agency',payload=json.dumps(dict(category_tag='ict_labour',title=title,supplier_state='ACT')),start=start,end=end,value=value,url='https://example.invalid/'+key))
            sql=module.RATE_SQL.replace('CREATE VIEW market.v_rate_benchmarks','CREATE TEMP VIEW test_rate_benchmarks').replace('market.current_analytical_contracts','rate_contracts')
            c.execute(text(sql));row=c.execute(text('SELECT * FROM test_rate_benchmarks')).mappings().one()
            self.assertEqual(row['sample_size'],3);self.assertAlmostEqual(float(row['estimated_calendar_day_rate']),4000/3)
            self.assertIn('Supplier state',row['state_basis'])
            self.assertIn('heuristics',row['estimate_basis']);self.assertEqual(row['target_roles'],'ict_labour')
            c.execute(text('DROP VIEW test_rate_benchmarks'))


if __name__=='__main__':unittest.main()
