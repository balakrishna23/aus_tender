"""Uncertain sends confirmed by Gmail Sent are settled before collection; nothing else is."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import subprocess
import sys
import tempfile
import unittest

deploy = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(deploy))
spec = importlib.util.spec_from_file_location("cloud_collect", deploy / "illoura_cloud_collect.py")
collect = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collect)


class ReconcileBeforeCollection(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.reports = Path(temp.name) / "reports"
        self.reports.mkdir()

    def run_folder(self, name, *files, decision=None):
        folder = self.reports / name
        folder.mkdir()
        for file in files:
            # A receipt settles an attempt only when it says the brief was sent (14 Nov cross test).
            (folder / file).write_text('{"sent": true}' if file == "send_receipt.json" else "{}")
        if decision is not None:
            (folder / "send_reconciliation.json").write_text(json.dumps(decision))
        return folder

    def test_only_attempts_without_receipt_or_decision_are_unresolved(self):
        self.run_folder("daily_sweep_20261112_141029", "send_attempt.json", "send_receipt.json")
        self.run_folder("daily_sweep_20261113_004506")
        self.run_folder("daily_sweep_20261114_004500", "send_attempt.json", decision={"delivered": False})
        open_one = self.run_folder("daily_sweep_20261115_004500", "send_attempt.json")
        self.run_folder("shared_official_sources", "send_attempt.json")
        self.assertEqual(collect.unresolved_send_runs(self.reports), [open_one])

    def test_runs_reconcile_as_scraper_without_mark_undelivered(self):
        folder = self.run_folder("daily_sweep_20261115_004500", "send_attempt.json")
        calls = []

        def runner(argv, **kwargs):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout='{"status": "delivered", "message_id": "m"}\n')

        self.assertEqual(collect.reconcile_confirmed_sends(self.reports, runner),
                         {folder.name: "delivered"})
        argv = calls[0]
        self.assertEqual(argv[:4], ["runuser", "-u", "scraper", "--"])
        self.assertEqual(argv[-3:], ["reconcile", "--out", str(folder)])
        self.assertNotIn("--mark-undelivered", argv)
        self.assertNotIn("send", argv)

    def test_not_found_and_errors_are_recorded_not_raised(self):
        self.run_folder("daily_sweep_20261115_004500", "send_attempt.json")
        self.run_folder("daily_sweep_20261116_004500", "send_attempt.json")
        answers = [SimpleNamespace(returncode=2, stdout='{"status": "not_found_in_sent"}\n'),
                   subprocess.TimeoutExpired("reconcile", 300)]

        def runner(argv, **kwargs):
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer

        self.assertEqual(collect.reconcile_confirmed_sends(self.reports, runner), {
            "daily_sweep_20261115_004500": "not_found_in_sent",
            "daily_sweep_20261116_004500": "error: TimeoutExpired",
        })

    def test_nothing_unresolved_runs_nothing(self):
        self.run_folder("daily_sweep_20261112_141029", "send_attempt.json", "send_receipt.json")
        self.assertEqual(collect.reconcile_confirmed_sends(self.reports, lambda *a, **k: self.fail()), {})


if __name__ == "__main__":
    unittest.main()
