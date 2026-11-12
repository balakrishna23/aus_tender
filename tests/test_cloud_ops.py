"""Publication failure and recovery tests, with isolated source/run fixtures."""

import importlib.util
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

spec = importlib.util.spec_from_file_location(
    "cloud_ops", Path(__file__).resolve().parents[1] / "deploy/illoura_cloud_ops.py"
)
ops = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ops)


class CloudOperations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = self.root / "project/databases"
        self.data.mkdir(parents=True)
        self.work = self.root / "work"
        self.run = self.work / "reports/daily_sweep_test"
        self.run.mkdir(parents=True)
        self.served = self.root / "served"
        self.policy = {"contract_api": ["contracts"]}
        with closing(sqlite3.connect(self.data / "contract_api.db")) as db, db:
            db.executescript(
                'CREATE TABLE contracts(id TEXT); INSERT INTO contracts VALUES ("one");'
            )
        self.write(
            "source_refresh.json",
            {
                "success": True,
                "feed": "rebuilt",
                "results": [
                    {"spider": s, "status": "OK", "errors": 0} for s in ops.EXPECTED
                ],
            },
        )
        self.write(
            "finalization.json",
            {
                "steps": [
                    {"step": s, "exit_code": 0}
                    for s in ["intelligence", "data_quality", "newsletter_evidence"]
                ]
            },
        )
        self.write(
            "newsletter_evidence.json",
            {
                "generated_at_utc": ops.utc(),
                "build_info": [{"built_at": "fixture-build"}],
            },
        )

    def write(self, name, value):
        ops.atomic_json(self.run / name, value)

    def publish(self):
        return ops.publish(self.data, self.served, self.run, self.policy)

    @unittest.skipIf(
        os.name == "nt",
        "Atomic symlink publication is verified on the Linux deployment",
    )
    def test_failed_source_does_not_replace_generation(self):
        self.publish()
        old = (self.served / "current").resolve()
        report = json.loads((self.run / "source_refresh.json").read_text())
        report["results"][0]["status"] = "FAILED"
        self.write("source_refresh.json", report)
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual((self.served / "current").resolve(), old)

    @unittest.skipIf(
        os.name == "nt",
        "Atomic symlink publication is verified on the Linux deployment",
    )
    def test_missing_table_leaves_old_generation_available(self):
        self.publish()
        old = (self.served / "current").resolve()
        self.policy["contract_api"].append("missing")
        with self.assertRaises(ValueError):
            self.publish()
        self.assertEqual((self.served / "current").resolve(), old)

    @unittest.skipIf(
        os.name == "nt",
        "Atomic symlink publication is verified on the Linux deployment",
    )
    def test_rollback_preserves_previous_data(self):
        self.publish()
        old = (self.served / "current").resolve()
        with closing(sqlite3.connect(self.data / "contract_api.db")) as db, db:
            db.execute('INSERT INTO contracts VALUES ("two")')
        self.publish()
        with closing(sqlite3.connect(self.served / "current/contract_api.db")) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM contracts").fetchone()[0], 2
            )
        ops.rollback(self.served)
        self.assertEqual((self.served / "current").resolve(), old)
        with closing(sqlite3.connect(self.served / "current/contract_api.db")) as db:
            self.assertEqual(
                db.execute("SELECT count(*) FROM contracts").fetchone()[0], 1
            )

    def test_restore_preserves_sent_and_attempt_state_without_credentials(self):
        self.write("send_receipt.json", {"sent": True, "message_id": "test-sent"})
        self.write("send_attempt.json", {"message_id_header": "test-attempt"})
        (self.work / "gmail-token.json").write_text("never copy")
        backup = ops.backup_operations(
            self.data.parent, self.work, self.root / "backups"
        )
        result = ops.restore_drill(backup)
        self.assertEqual((result["send_receipts"], result["send_attempts"]), (1, 1))
        self.assertFalse(result["mail_replay_performed"])
        self.assertFalse(list(backup.rglob("*token*")))

    def test_corrupt_backup_is_rejected(self):
        self.write("send_receipt.json", {"sent": True})
        backup = ops.backup_operations(
            self.data.parent, self.work, self.root / "backups"
        )
        (backup / "reports" / self.run.name / "send_receipt.json").write_text("{}")
        with self.assertRaises(ValueError):
            ops.restore_drill(backup)

    def test_evidence_without_build_timestamp_is_rejected(self):
        self.write("newsletter_evidence.json", {"generated_at_utc": ops.utc()})
        with self.assertRaises(ValueError):
            ops.validate_run(self.run)

    def test_unresolved_send_survives_restore_and_requires_reconciliation(self):
        self.write("send_attempt.json", {"message_id_header": "uncertain"})
        backup = ops.backup_operations(
            self.data.parent, self.work, self.root / "backups"
        )
        result = ops.restore_drill(backup)
        self.assertEqual(result["unresolved_send_runs"], ["reports/" + self.run.name])
        self.assertTrue(result["requires_mail_sent_reconciliation_before_resume"])
        self.assertEqual(result["status"], "attention")

    def test_resolved_send_still_passes(self):
        self.write("send_attempt.json", {"message_id_header": "done"})
        self.write("send_receipt.json", {"sent": True})
        backup = ops.backup_operations(
            self.data.parent, self.work, self.root / "backups"
        )
        self.assertEqual(ops.restore_drill(backup)["status"], "passed")

    def test_restore_drill_cli_exits_nonzero_on_attention(self):
        from contextlib import contextmanager, redirect_stdout
        from unittest.mock import patch
        import io

        @contextmanager
        def no_lock(path):
            yield

        with (
            patch("sys.argv", ["ops", "restore-drill", "--backup", str(self.root)]),
            patch.object(ops, "exclusive", side_effect=no_lock),
            patch.object(
                ops,
                "restore_drill",
                return_value={"status": "attention", "unresolved_send_runs": ["x"]},
            ),
            redirect_stdout(io.StringIO()),
        ):
            with self.assertRaises(SystemExit) as stopped:
                ops.main()
        self.assertEqual(stopped.exception.code, 1)

    @unittest.skipIf(
        os.name == "nt",
        "Atomic symlink publication is verified on the Linux deployment",
    )
    def test_second_rollback_returns_to_newer_generation(self):
        self.publish()
        first = (self.served / "current").resolve()
        self.publish()
        second = (self.served / "current").resolve()
        result = ops.rollback(self.served)
        self.assertEqual((self.served / "current").resolve(), first)
        self.assertEqual((self.served / "previous").resolve(), second)
        self.assertEqual(result["previous_generation"], second.name)
        ops.rollback(self.served)
        self.assertEqual((self.served / "current").resolve(), second)
        self.assertEqual((self.served / "previous").resolve(), first)

    @unittest.skipIf(
        os.name == "nt",
        "Atomic symlink publication is verified on the Linux deployment",
    )
    def test_rollback_refuses_when_pointers_name_the_same_generation(self):
        self.publish()
        only = (self.served / "current").resolve()
        (self.served / "previous").symlink_to(only.name, target_is_directory=True)
        with self.assertRaises(ValueError):
            ops.rollback(self.served)
        self.assertEqual((self.served / "current").resolve(), only)


if __name__ == "__main__":
    unittest.main()
