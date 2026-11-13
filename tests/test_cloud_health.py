"""A failed new run must be visible even while yesterday's data is still fresh."""

import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

deploy = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(deploy))
spec = importlib.util.spec_from_file_location(
    "cloud_health", deploy / "illoura_cloud_health.py"
)
health = importlib.util.module_from_spec(spec)
spec.loader.exec_module(health)


class CloudHealth(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.work = self.root / "work"
        self.served = self.root / "served"
        self.run = self.work / "reports/daily_sweep_fixture"
        self.run.mkdir(parents=True)
        (self.served / "current").mkdir(parents=True)
        stamp = datetime.now(timezone.utc).isoformat()
        self.write(
            self.work / "latest_run.json", {"run_dir": str(self.run), "status": "sent"}
        )
        self.write(self.run / "newsletter_evidence.json", {"generated_at_utc": stamp})
        self.write(
            self.served / "current/manifest.json", {"snapshot_finished_utc": stamp}
        )

    def write(self, path, value):
        path.write_text(json.dumps(value))

    def check(self, brief_enabled=False, now=None):
        # brief_enabled is explicit so staged runs on the server never read its real timer.
        return health.check(
            self.work,
            self.served,
            self.root / "missing-token.json",
            brief_enabled=brief_enabled,
            now=now,
            cert=None,  # certificate covered by CertificateExpiry
        )

    def test_failure_is_not_hidden_by_fresh_previous_run(self):
        self.write(
            self.work / "coordinator_status.json",
            {
                "status": "failed",
                "started_at_utc": datetime.now(timezone.utc).isoformat(),
            },
        )
        result = self.check()
        self.assertEqual(result["checks"]["publication"]["status"], "ok")
        self.assertEqual(result["checks"]["coordinator"]["status"], "attention")
        self.assertEqual(result["status"], "attention")

    def test_hung_coordinator_requires_attention(self):
        self.write(
            self.work / "coordinator_status.json",
            {
                "status": "running",
                "started_at_utc": (
                    datetime.now(timezone.utc) - timedelta(hours=6)
                ).isoformat(),
            },
        )
        self.assertEqual(self.check()["checks"]["coordinator"]["status"], "attention")

    def test_missing_mail_credentials_are_explicit(self):
        with patch.dict("os.environ", {"ILLOURA_GMAIL_SENDER": "alerts@example.com"}):
            result = self.check()
        self.assertEqual(result["checks"]["mail_authorization"]["status"], "failed")
        self.assertFalse(result["email_sent"])

    def test_unconfigured_sender_requires_attention_without_crashing(self):
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ILLOURA_GMAIL_SENDER", None)
            result = self.check()
        self.assertEqual(
            result["checks"]["mail_authorization"]["status"], "unconfigured"
        )
        self.assertEqual(result["status"], "attention")
        self.assertFalse(result["email_sent"])

    def test_source_contains_no_real_mailbox(self):
        self.assertNotIn(
            "illouraservices.com",
            (deploy / "illoura_cloud_health.py").read_text(encoding="utf-8"),
        )

    def test_retention_reclaimed_space_is_reported(self):
        self.write(
            self.served / "retention.json",
            {
                "status": "ok",
                "reclaimed_bytes": 4096,
                "observed_free_bytes_change": 2048,
                "retained_generation_bytes": 8000,
                "retained_backup_bytes": 2000,
                "deleted": [{"name": "fixture"}],
            },
        )
        result = self.check()["checks"]["retention"]
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["reclaimed_bytes"], 4096)
        self.assertEqual(result["observed_free_bytes_change"], 2048)

    def test_retention_failure_requires_attention(self):
        self.write(
            self.served / "retention.json",
            {"status": "attention", "errors": [{"error_type": "OSError"}]},
        )
        self.assertEqual(self.check()["checks"]["retention"]["status"], "attention")


SYDNEY = health.SYDNEY


def sydney(day, hour, minute=0):
    """A UTC instant for a Sydney wall-clock time in November 2026 (AEDT)."""
    return datetime(2026, 11, day, hour, minute, tzinfo=SYDNEY).astimezone(timezone.utc)


class MorningRunAndDailyBrief(unittest.TestCase):
    """13 November 2026: the brief was skipped and nobody was told; a normal run must not alarm."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / "work"
        (self.work / "reports").mkdir(parents=True)

    def run_dir(self, name, status, started, receipt_at=None):
        run = self.work / "reports" / name
        run.mkdir()
        state = {"run_dir": str(run), "status": status, "started_at_utc": started.isoformat()}
        (self.work / "latest_run.json").write_text(json.dumps(state))
        if receipt_at:
            receipt = {"sent": True, "recorded_at_utc": receipt_at.isoformat()}
            (run / "send_receipt.json").write_text(json.dumps(receipt))
        return run

    def brief(self, now, enabled=True):
        return health.daily_brief(self.work, now, enabled)

    def test_brief_sent_today_is_ok_after_nine(self):
        self.run_dir("daily_sweep_a", "sent", sydney(14, 6, 15), receipt_at=sydney(14, 8, 15))
        self.assertEqual(self.brief(sydney(14, 9, 30))["status"], "ok")

    def test_skipped_brief_is_missed_after_nine(self):
        self.run_dir("daily_sweep_a", "sent", sydney(12, 19), receipt_at=sydney(12, 20))
        self.run_dir("daily_sweep_b", "source_failure", sydney(13, 6, 15))
        result = self.brief(sydney(13, 16, 17))
        self.assertEqual(result["status"], "missed")
        self.assertTrue(result["due_by_sydney"].startswith("2026-11-13T09:00"))

    def test_yesterdays_brief_covers_the_morning_while_todays_run_works(self):
        self.run_dir("daily_sweep_a", "sent", sydney(13, 6, 15), receipt_at=sydney(13, 8, 15))
        self.run_dir("daily_sweep_b", "running_sources", sydney(14, 6, 15))
        self.assertEqual(self.brief(sydney(14, 7, 0))["status"], "ok")

    def test_schedule_off_never_alarms(self):
        self.assertEqual(self.brief(sydney(14, 12), enabled=False)["status"], "ok")

    def test_unsent_receipts_do_not_count(self):
        run = self.run_dir("daily_sweep_a", "sent", sydney(14, 6, 15))
        (run / "send_receipt.json").write_text(
            json.dumps({"sent": False, "recorded_at_utc": sydney(14, 8, 15).isoformat()})
        )
        self.assertEqual(self.brief(sydney(14, 10))["status"], "missed")

    def test_morning_collection_in_progress_is_healthy_until_five_hours(self):
        started = sydney(14, 6, 15)
        state = {"status": "running_sources", "started_at_utc": started.isoformat()}
        self.assertTrue(health.run_in_progress(state, started + timedelta(hours=2)))
        self.assertFalse(health.run_in_progress(state, started + timedelta(hours=6)))
        self.assertFalse(health.run_in_progress({**state, "status": "source_failure"}, started))

    def test_whole_check_during_collection_is_ok(self):
        served = Path(self.temp.name) / "served"
        (served / "current").mkdir(parents=True)
        now = sydney(14, 7, 0)
        (served / "current/manifest.json").write_text(
            json.dumps({"snapshot_finished_utc": (now - timedelta(hours=23)).isoformat()})
        )
        self.run_dir("daily_sweep_a", "sent", sydney(13, 6, 15), receipt_at=sydney(13, 8, 15))
        self.run_dir("daily_sweep_b", "building_evidence", sydney(14, 6, 15))
        with patch.dict("os.environ", {}, clear=False):
            os.environ.pop("ILLOURA_GMAIL_SENDER", None)
            checks = health.check(self.work, served, Path(self.temp.name) / "t.json",
                                  brief_enabled=True, now=now,
                                  cert=None)["checks"]
        for name in ("publication", "collection", "last_run", "daily_brief"):
            self.assertEqual(checks[name]["status"], "ok", name)


class CertificateExpiry(unittest.TestCase):
    """The 6-day HTTPS certificate must warn a day ahead, not when the external watch fails."""

    now = datetime(2026, 11, 13, 7, 0, tzinfo=timezone.utc)

    def status(self, hours_left):
        end = self.now + timedelta(hours=hours_left)
        return health.certificate(Path("cert.pem"), self.now, read_end=lambda _: end)

    def test_normal_lifetime_is_ok(self):
        self.assertEqual(self.status(60)["status"], "ok")
        self.assertEqual(self.status(25)["status"], "ok")

    def test_under_a_day_left_is_expiring(self):
        self.assertEqual(self.status(23)["status"], "expiring")

    def test_expired(self):
        self.assertEqual(self.status(-1)["status"], "expired")

    def test_unreadable_certificate(self):
        def broken(_):
            raise OSError("permission denied")

        self.assertEqual(
            health.certificate(Path("cert.pem"), self.now, read_end=broken)["status"],
            "unavailable",
        )

    def test_openssl_date_format_is_parsed(self):
        result = type("R", (), {"stdout": "notAfter=Nov  8 22:59:29 2026 GMT\n"})()
        with tempfile.TemporaryDirectory() as temp, \
                patch.object(health.subprocess, "run", return_value=result):
            cert = Path(temp) / "cert.pem"
            cert.write_text("placeholder")
            end = health.cert_not_after(cert)
        self.assertEqual(end, datetime(2026, 11, 8, 22, 59, 29, tzinfo=timezone.utc))

    def test_missing_certificate_fails_health(self):
        # Codex review of PR #3: a missing file must not silently drop the check.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "served").mkdir()
            result = health.check(
                root / "work", root / "served", root / "token.json",
                brief_enabled=False, now=self.now, cert=root / "gone.pem",
            )
        self.assertEqual(result["checks"]["certificate"]["status"], "missing")
        self.assertEqual(result["status"], "attention")

    def test_expiring_certificate_makes_health_attention(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "served").mkdir()
            cert = root / "cert.pem"
            cert.write_text("placeholder")
            result = health.check(
                root / "work", root / "served", root / "token.json",
                brief_enabled=False, now=self.now, cert=cert,
                read_cert_end=lambda _: self.now + timedelta(hours=5),
            )
        self.assertEqual(result["checks"]["certificate"]["status"], "expiring")
        self.assertEqual(result["status"], "attention")


if __name__ == "__main__":
    unittest.main()
