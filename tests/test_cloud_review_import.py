"""Root wrapper scheduling boundaries, offline on Windows and Linux."""
from datetime import datetime
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

spec = importlib.util.spec_from_file_location('review_worker', Path(__file__).resolve().parents[1] / 'deploy/illoura_review_import.py')
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class ReviewWrapperTests(unittest.TestCase):
    def test_blackout_boundaries_and_dst(self):
        for day in ('2026-07-01', '2026-11-27'):
            for clock, expected in [('05:27', False), ('05:28', True), ('05:30', True), ('09:29', True), ('09:30', False)]:
                self.assertEqual(worker.blackout(datetime.fromisoformat(day + 'T' + clock).replace(
                    tzinfo=ZoneInfo('Australia/Sydney'))), expected)

    def test_busy_lock_defers_without_starting_application(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = SimpleNamespace(flock=Mock(side_effect=BlockingIOError), LOCK_EX=2, LOCK_NB=4)
            with patch.dict(sys.modules, {'fcntl': fake}), patch.object(worker, 'LOCK', root / 'lock'), patch.object(
                worker, 'blackout', return_value=False), patch.object(worker, 'configuration', return_value=(root, root)), patch.object(worker, 'run_application') as run:
                self.assertEqual(worker.run()['status'], 'deferred')
                run.assert_not_called()
                self.assertEqual(fake.flock.call_args.args[1], 6)

    def test_blackout_defers_and_is_rechecked_under_lock(self):
        fake = SimpleNamespace(flock=Mock(), LOCK_EX=2, LOCK_NB=4)
        with tempfile.TemporaryDirectory() as tmp, patch.dict(sys.modules, {'fcntl': fake}), patch.object(
            worker, 'LOCK', Path(tmp) / 'lock'), patch.object(worker, 'configuration', return_value=(Path(tmp), Path(tmp))), patch.object(
            worker, 'blackout', side_effect=[False, True]), patch.object(worker, 'run_application') as run:
            self.assertEqual(worker.run()['reason'], 'sydney_brief_blackout')
            run.assert_not_called()

    def test_application_is_unprivileged_and_db_secret_not_in_argv(self):
        process = Mock(returncode=0)
        with patch.object(worker.subprocess, 'Popen', return_value=process) as launch:
            self.assertEqual(worker.run_application(Path('/home/ubuntu/illoura-market-data'), Path('/build'))['status'], 'complete')
            argv = launch.call_args.args[0]
            self.assertEqual(argv[:4], ['runuser', '-u', 'ubuntu', '--'])
            self.assertIn('--environment', argv)
            self.assertFalse(any('postgresql:' in s for s in argv))
            process.wait.assert_called_once_with(timeout=115)

    def test_deferred_exit_retries_and_unit_is_bounded(self):
        with patch.object(worker, 'run', return_value={'status': 'deferred'}):
            self.assertEqual(worker.main([]), 75)
        service = (Path(__file__).resolve().parents[1] / 'deploy/illoura-review-import.service').read_text()
        self.assertIn('Restart=on-failure', service)
        self.assertIn('TimeoutStartSec=130', service)
