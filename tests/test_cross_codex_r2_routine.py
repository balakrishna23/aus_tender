"""Deadline and exclusive mail-config crash recovery, without Linux dependencies."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import test_routine_mail_env as fixtures

helper = fixtures.helper
spec = importlib.util.spec_from_file_location('r2_run_once', fixtures.ROOT / 'routine/run_once.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class DeadlineRoundTwo(unittest.TestCase):
    def test_invalid_and_nonfinite_deadlines_fall_back(self):
        for raw in ['', 'bad', 'nan', 'inf', '-inf', None]:
            with self.subTest(raw=raw):
                self.assertEqual(runner.routine_deadline({'ILLOURA_MORNING_DEADLINE': raw}, 10, 100), 10 + runner.ROUTINE_BUDGET_SECONDS)

    def test_elapsed_shorter_and_longer_budgets(self):
        for epoch, expected in [('99', 9), ('120', 30), ('999999', 10 + runner.ROUTINE_BUDGET_SECONDS)]:
            with self.subTest(epoch=epoch):
                self.assertEqual(runner.routine_deadline({'ILLOURA_MORNING_DEADLINE': epoch}, 10, 100), expected)


class MailEnvCrashRecovery(unittest.TestCase):
    def test_leftover_temporary_does_not_prevent_next_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'mail.env'
            leftover = target.with_name('.mail.env.old.tmp')
            leftover.write_text('partial synthetic')
            self.assertEqual(helper.write(target, 'synthetic', group=None), 'written')
            self.assertEqual(leftover.read_text(), 'partial synthetic')
            self.assertEqual(target.read_text(), 'synthetic')

    def test_chown_failure_removes_private_temporary(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'mail.env'
            fake_grp = SimpleNamespace(getgrnam=lambda _: SimpleNamespace(gr_gid=1))
            with patch.dict(sys.modules, {'grp': fake_grp}), \
                    patch.object(helper.os, 'chown', create=True, side_effect=PermissionError('synthetic')):
                with self.assertRaises(PermissionError):
                    helper.write(target, 'synthetic-private-value')
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(tmp).iterdir()), [])
