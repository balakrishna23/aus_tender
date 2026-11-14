"""Uncertain send recovery must remain conservative and nonfatal."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'deploy'))
spec = importlib.util.spec_from_file_location('cross_collect', ROOT / 'deploy/illoura_cloud_collect.py')
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


class UncertainSend(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(tmp.cleanup)
        self.reports = Path(tmp.name)
        self.run = self.reports / 'daily_sweep_fixture'
        self.run.mkdir()
        (self.run / 'send_attempt.json').write_text('{}')

    def test_false_receipt_remains_unresolved(self):
        """A receipt explicitly saying not sent must not unlock publication."""
        (self.run / 'send_receipt.json').write_text('{"sent":false}')
        self.assertEqual(c.unresolved_send_runs(self.reports), [self.run])

    def test_corrupt_receipt_remains_unresolved(self):
        """An interrupted receipt write must not count as confirmed delivery."""
        (self.run / 'send_receipt.json').write_text('{')
        self.assertEqual(c.unresolved_send_runs(self.reports), [self.run])

    def test_wrong_type_decision_does_not_abort_recovery(self):
        """Malformed reconciliation must not stop recovery of other runs."""
        (self.run / 'send_reconciliation.json').write_text('[]')
        runner = Mock(return_value=Mock(stdout='{"status":"delivered"}', returncode=0))
        self.assertEqual(c.reconcile_confirmed_sends(self.reports, runner), {self.run.name: 'delivered'})

    def test_runner_errors_leave_attempt_unresolved(self):
        """Gmail outages must never initiate a send or silently settle uncertainty."""
        runner = Mock(side_effect=TimeoutError())
        self.assertEqual(c.reconcile_confirmed_sends(self.reports, runner), {self.run.name: 'error: TimeoutError'})
        self.assertEqual(c.unresolved_send_runs(self.reports), [self.run])
        argv = runner.call_args.args[0]
        self.assertIn('reconcile', argv)
        self.assertNotIn('--mark-undelivered', argv)

    def test_explicit_not_delivered_settles(self):
        """An operator's explicit decision must unblock later publication."""
        (self.run / 'send_reconciliation.json').write_text(json.dumps({'delivered': False}))
        self.assertEqual(c.unresolved_send_runs(self.reports), [])
