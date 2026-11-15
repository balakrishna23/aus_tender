"""Execute the original top-level runner with isolated paths and fake subprocesses."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import runpy
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]


class RunOnceTests(unittest.TestCase):
    def run_case(self, source_exit=0, success=True, count=8, feed=True, final_exit=0, steps=None, research_exit=0, timeout_stage=None, expired_budget=False, results=None, feed_text='rebuilt', drop_success=False):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, work = base/'project', base/'work'
            work.mkdir()
            real_path = type(project)
            def path(value):
                return {'/opt/aus_tender': project, '/opt/illoura-routine': work}.get(value, real_path(value))
            def process(args, **kwargs):
                self.calls.append((args, kwargs))
                stage = 'source' if 'run_spiders.py' in args else 'finalization' if any('finalize_daily_sweep.py' in a for a in args) else 'research'
                if stage == timeout_stage:
                    out = next((work/'reports').iterdir())
                    if stage == 'finalization':
                        (out/'finalization.json').write_text('{partial')
                    raise __import__('subprocess').TimeoutExpired(args, kwargs['timeout'])
                out = next((work/'reports').iterdir())
                if 'run_spiders.py' in args:
                    report = {'success': success, 'results': results if results is not None else self.results()[:count]}
                    if feed:
                        report['feed'] = feed_text
                    if drop_success:
                        del report['success']
                    (out/'source_refresh.json').write_text(json.dumps(report))
                    return SimpleNamespace(returncode=source_exit)
                if any('finalize_daily_sweep.py' in a for a in args):
                    (out/'finalization.json').write_text(json.dumps({'steps': steps if steps is not None else
                        [{'step': name, 'exit_code': 0} for name in ('intelligence', 'data_quality', 'newsletter_evidence')]}))
                    return SimpleNamespace(returncode=final_exit)
                return SimpleNamespace(returncode=research_exit)
            database = Mock()
            database.__enter__ = Mock(return_value=database)
            database.__exit__ = Mock(return_value=False)
            database.execute.return_value.fetchone.return_value = (0,)
            database.execute.return_value.__iter__ = Mock(return_value=iter([]))
            self.calls = []
            def popen(args, **kwargs):
                child = Mock(pid=99999999)
                child.wait.side_effect = lambda timeout=None: process(args, **dict(kwargs, timeout=timeout)).returncode if timeout is not None else 0
                return child
            with patch.dict('sys.modules', {'fcntl': Mock(LOCK_EX=2, LOCK_NB=4)}), patch('pathlib.Path', side_effect=path), patch('sqlite3.connect', return_value=database), \
                 patch('subprocess.Popen', side_effect=popen), patch.dict(os.environ, {'ILLOURA_MORNING_DEADLINE': '1'} if expired_budget else {}), redirect_stdout(io.StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(ROOT/'routine/run_once.py'), run_name='__main__')
            return raised.exception.code, json.loads((work/'latest_run.json').read_text())

    def test_success_and_research_failure_does_not_change_gate(self):
        code, state = self.run_case(research_exit=1)
        self.assertEqual(code, 0)
        self.assertEqual(state['status'], 'ready_for_editorial_review')
        self.assertEqual(state['research_exit_code'], 1)

    def test_source_gates(self):
        for kwargs in ({'source_exit': 1}, {'success': False}, {'count': 7}, {'feed': False}):
            with self.subTest(kwargs=kwargs):
                code, state = self.run_case(**kwargs)
                self.assertEqual((code, state['status']), (1, 'source_failure'))

    NAMES = ('contract_api', 'aus_contract', 'aus_tender', 'planned_procurement', 'standing_offer', 'buy_ict', 'act_tender', 'nsw_tender')

    def results(self, **problems):
        return [dict({'spider': name, 'status': 'OK'}, **problems.get(name, {})) for name in self.NAMES]

    def test_problem_in_a_source_the_brief_does_not_use_continues(self):
        # 15 Nov 2026: planned_procurement PARTIAL cancelled the brief although BuyICT was fine.
        failed = self.results(planned_procurement={'status': 'PARTIAL', 'crawl_failed': True, 'errors': 1})
        code, state = self.run_case(source_exit=1, success=False, results=failed)
        self.assertEqual((code, state['status']), (0, 'ready_for_editorial_review'))
        self.assertEqual(state['source_gaps'], ['planned_procurement'])

    def test_buyict_problem_or_unclear_run_still_stops(self):
        cases = {
            'buy_ict failed': dict(source_exit=1, success=False, results=self.results(buy_ict={'status': 'FAIL(1)'})),
            'buy_ict among several': dict(source_exit=1, success=False, results=self.results(
                buy_ict={'status': 'PARTIAL', 'crawl_failed': True}, nsw_tender={'status': 'FAIL(1)'})),
            'failure with no named source': dict(source_exit=1, success=False, results=self.results()),
            'cut short': dict(source_exit=124, success=False, results=self.results(nsw_tender={'status': 'FAIL(124)'})),
            'feed failed as well': dict(source_exit=1, success=False, feed_text='releases feed: FAILED (disk error); intelligence views rebuilt',
                                        results=self.results(planned_procurement={'status': 'PARTIAL'})),
            'views failed as well': dict(source_exit=1, success=False, feed_text='releases feed: 0 new; intelligence views FAILED (x)',
                                         results=self.results(planned_procurement={'status': 'PARTIAL'})),
            'success flag missing': dict(source_exit=1, drop_success=True, results=self.results(planned_procurement={'status': 'PARTIAL'})),
            'gap but runner says success': dict(source_exit=1, success=True, results=self.results(planned_procurement={'status': 'PARTIAL'})),
            'unknown source names': dict(source_exit=1, success=False, results=[{'spider': str(i), 'status': 'FAIL(1)'} for i in range(8)]),
            'seven sources': dict(source_exit=1, success=False, results=self.results(nsw_tender={'status': 'FAIL(1)'})[:7]),
        }
        for label, kwargs in cases.items():
            with self.subTest(label):
                code, state = self.run_case(**kwargs)
                self.assertEqual((code, state['status']), (1, 'source_failure'))

    def test_one_unrelated_source_timing_out_continues(self):
        code, state = self.run_case(source_exit=1, success=False, results=self.results(nsw_tender={'status': 'FAIL(124)', 'exit_code': 124}))
        self.assertEqual((code, state['status'], state['source_gaps']), (0, 'ready_for_editorial_review', ['nsw_tender']))

    def test_clean_run_records_no_gaps(self):
        code, state = self.run_case(results=self.results())
        self.assertEqual(code, 0)
        self.assertNotIn('source_gaps', state)

    def test_evidence_gates(self):
        for kwargs in ({'final_exit': 1}, {'steps': []}, {'steps': [{'step': 'intelligence', 'exit_code': 1}]}):
            with self.subTest(kwargs=kwargs):
                code, state = self.run_case(**kwargs)
                self.assertEqual((code, state['status']), (1, 'evidence_failure'))

    def test_deadlines_and_spider_arguments(self):
        self.run_case()
        calls = {('source' if 'run_spiders.py' in args else 'finalization' if any('finalize_daily_sweep.py' in a for a in args) else 'research'): (args, kw) for args, kw in self.calls}
        self.assertEqual(calls['source'][0][4], '2700')
        self.assertIn('--retries', calls['source'][0])
        for stage, limit in [('source', 3600), ('research', 3600), ('finalization', 2700)]:
            self.assertGreater(calls[stage][1]['timeout'], 0)
            self.assertLessEqual(calls[stage][1]['timeout'], limit)
            self.assertEqual(calls[stage][1]['start_new_session'], os.name != 'nt')

    def test_timeout_statuses_without_completed_reports(self):
        for stage, status, code in [('source', 'source_failure', 1), ('research', 'ready_for_editorial_review', 0), ('finalization', 'evidence_failure', 1)]:
            with self.subTest(stage=stage):
                result, state = self.run_case(timeout_stage=stage)
                self.assertEqual((result, state['status']), (code, status))
                self.assertEqual(state[stage + '_exit_code'], 124)


    def test_absolute_expired_budget_starts_no_children(self):
        result, state = self.run_case(expired_budget=True)
        self.assertEqual((result, state['status']), (1, 'source_failure'))
        self.assertEqual(self.calls, [])
        self.assertEqual(state['research_exit_code'], 124)
