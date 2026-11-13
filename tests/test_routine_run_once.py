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


@unittest.skipIf(os.name == 'nt', 'Server runner requires Linux fcntl')
class RunOnceTests(unittest.TestCase):
    def run_case(self, source_exit=0, success=True, count=8, feed=True, final_exit=0, steps=None, research_exit=0):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            project, work = base/'project', base/'work'
            work.mkdir()
            real_path = Path
            def path(value):
                return {'/opt/aus_tender': project, '/opt/illoura-routine': work}.get(value, real_path(value))
            def process(args, **kwargs):
                out = next((work/'reports').iterdir())
                if 'run_spiders.py' in args:
                    report = {'success': success, 'results': [{}]*count}
                    if feed:
                        report['feed'] = 'rebuilt'
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
            with patch('pathlib.Path', side_effect=path), patch('sqlite3.connect', return_value=database), \
                 patch('subprocess.run', side_effect=process), patch.dict(os.environ), redirect_stdout(io.StringIO()):
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

    def test_evidence_gates(self):
        for kwargs in ({'final_exit': 1}, {'steps': []}, {'steps': [{'step': 'intelligence', 'exit_code': 1}]}):
            with self.subTest(kwargs=kwargs):
                code, state = self.run_case(**kwargs)
                self.assertEqual((code, state['status']), (1, 'evidence_failure'))
