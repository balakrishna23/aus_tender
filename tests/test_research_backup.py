"""Research + Metabase recovery set: full restore comparison, durable evidence, no secrets, no deletion."""
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "illoura_research_backup", Path(__file__).resolve().parents[1] / "deploy" / "illoura_research_backup.py"
)
rb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rb)

MARKET_PASSWORD = "s3cret-market-pass"
APP_PASSWORD = "mb-app-secret-value"
ENCRYPTION_KEY = "enc-key-secret-value"
ADDRESS = "10.1.2.3"
SECRETS = (MARKET_PASSWORD, APP_PASSWORD, ENCRYPTION_KEY, ADDRESS)


def market_state():
    return {
        "tables": {"market.alembic_version": 1, "market.contracts": 1200, "market.snapshots": 3,
                   "market.demand_snapshots": 2, "public.notes": 0},
        "matviews": {"market.mv_spend": 40},
        "hashes": {"market.snapshots": [3, "aaa"], "market.demand_snapshots": [2, "bbb"]},
        "revision": "018_research_views",
    }


def metabase_state():
    return {
        "columns": {
            "report_card": ["id", "name", "dataset_query", "archived", "last_used_at", "view_count"],
            "report_dashboard": ["id", "name", "archived"],
            "report_dashboardcard": ["id", "dashboard_id", "card_id"],
            "core_user": ["id", "is_active", "last_login"],
        },
        "content": {"report_card": [5, "c1"], "report_dashboard": [1, "d1"],
                    "report_dashboardcard": [5, "dc1"], "core_user": [2, "u1"]},
    }


class FakeServer:
    """Stands in for pg_dump/pg_restore/psql/sudo; a 'dump' is the JSON of a database's state."""

    def __init__(self):
        self.dbs = {"illoura_market": market_state(), "illoura_metabase": metabase_state()}
        self.commands = []
        self.hook = None

    def __call__(self, args, env):
        self.commands.append(list(args))
        if self.hook:
            self.hook(self, args, env)
        program = args[0]
        if program == "pg_dump":
            Path(args[args.index("--file") + 1]).write_text(json.dumps(self.dbs[env["PGDATABASE"]]))
            return ""
        if program == "pg_restore" and "--list" in args:
            json.loads(Path(args[-1]).read_text())
            return "; Archive created\n"
        if program == "pg_restore":
            target = args[args.index("--dbname") + 1]
            assert self.dbs[target] is None, "restore target must be a new empty database"
            self.dbs[target] = json.loads(Path(args[-1]).read_text())
            return ""
        if program == "sudo":
            match = re.fullmatch(r"CREATE DATABASE ([a-z0-9_]+) OWNER ([a-z0-9_]+)", args[-1])
            assert match and match[1] not in self.dbs
            self.dbs[match[1]] = None
            return ""
        if program == "psql":
            return self.query(self.dbs[env["PGDATABASE"]], args[args.index("-c") + 1])
        raise AssertionError(f"unexpected command {program}")

    @staticmethod
    def query(db, sql):
        def pipe(names):
            return "\n".join(n.replace(".", "|", 1) for n in names)

        if sql == rb.TABLES_SQL:
            return pipe(db["tables"])
        if sql == rb.MATVIEWS_SQL:
            return pipe(db["matviews"])
        if sql == rb.HASHED_TABLES_SQL:
            return pipe(db["hashes"])
        if sql == rb.METABASE_COLUMNS_SQL:
            return "\n".join(f"{t}|{c}" for t, cols in db["columns"].items() for c in cols)
        if sql.startswith("SELECT version_num"):
            return db["revision"]
        names = re.findall(r"SELECT '([a-z0-9_.]+)', count\(\*\)", sql)
        if "concat_ws" in sql:
            return "\n".join(f"{n}|{db['content'][n][0]}|{db['content'][n][1]}" for n in names)
        if "md5(" in sql:
            return "\n".join(f"{n}|{db['hashes'][n][0]}|{db['hashes'][n][1]}" for n in names)
        counts = {**db["tables"], **db["matviews"]}
        return "\n".join(f"{n}|{counts[n]}" for n in names)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 20, 14, 0, tzinfo=UTC)

    def __call__(self):
        self.now += timedelta(seconds=1)
        return self.now


class ResearchBackupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.env_file = self.root / "database.env"
        self.env_file.write_text(
            "# private\nILLOURA_MARKET_DATABASE_URL="
            f"postgresql+psycopg://illoura_market_owner:{MARKET_PASSWORD}@{ADDRESS}:5432/illoura_market\n"
        )
        self.meta = self.root / "metabase"
        self.meta.mkdir()
        (self.meta / "credentials.json").write_text(json.dumps(
            {"app_password": APP_PASSWORD, "admin_password": "admin-x", "reader_password": "reader-y"}))
        (self.meta / "metabase.env").write_text(
            f"MB_DB_PASS={APP_PASSWORD}\nMB_ENCRYPTION_SECRET_KEY={ENCRYPTION_KEY}\nMB_JETTY_HOST=127.0.0.1\n")
        (self.meta / "provisioned.json").write_text('{"dashboard": 2}')
        self.server = FakeServer()
        self.clock = Clock()

    def run_backup(self, name="set1", restore=True):
        return rb.backup(self.env_file, self.meta, self.root / "backups" / name, restore=restore,
                         runner=self.server, clock=self.clock, base_env={})

    def evidence(self, name="set1"):
        text = (self.root / "backups" / name / "verification.json").read_text()
        for secret in SECRETS:
            self.assertNotIn(secret, text)
        return json.loads(text)

    def assert_nothing_deleted(self):
        for command in self.server.commands:
            joined = " ".join(command).upper()
            self.assertNotIn("DROP", joined)
            self.assertNotIn("DROPDB", joined)
            self.assertNotIn("--CLEAN", joined)

    def test_complete_restore_drill_verifies_both_databases_and_recovery_files(self):
        result = self.run_backup()
        self.assertEqual(result["status"], "complete")
        evidence = self.evidence()
        self.assertEqual(evidence["status"], "complete")
        market, metabase = evidence["databases"]
        self.assertTrue(market["restore_verified"] and metabase["restore_verified"])
        self.assertEqual(market["live"]["schema_revision"], ["018_research_views"])
        self.assertEqual(market["live"]["relations_counted"], 6)
        self.assertEqual(market["live"]["rows_counted"], 1246)
        self.assertEqual(market["live"]["source_hash_tables"], 2)
        self.assertEqual(metabase["live"]["content_rows"]["report_card"], 5)
        self.assertEqual(self.server.dbs[market["restored_database"]], market_state())
        self.assertTrue(market["restored_database"].startswith("illoura_market_restore_20261120t"))
        backup_dir = self.root / "backups" / "set1"
        sums = (backup_dir / "SHA256SUMS").read_text().splitlines()
        self.assertEqual(len(sums), 5)
        for line in sums:
            digest, name = line.split("  ")
            self.assertEqual(rb.sha256_file(backup_dir / name), digest)
        self.assertEqual({r["name"] for r in evidence["recovery_files"]},
                         {"credentials.json", "metabase.env", "provisioned.json"})
        self.assertEqual((backup_dir / "metabase.env").read_text(), (self.meta / "metabase.env").read_text())
        self.assertNotIn("warnings", evidence)
        self.assert_nothing_deleted()

    def test_without_restore_drill_creates_no_databases(self):
        result = self.run_backup(restore=False)
        self.assertEqual(result["status"], "complete")
        self.assertFalse(any(c[0] == "sudo" for c in self.server.commands))
        self.assertTrue(all("restored_database" not in d for d in result["databases"]))

    def test_restored_count_difference_fails_with_named_table(self):
        original = FakeServer.__call__

        def restore_with_loss(args, env):
            output = original(self.server, args, env)
            if args[0] == "pg_restore" and "--dbname" in args:
                target = args[args.index("--dbname") + 1]
                if target.startswith("illoura_market_restore"):
                    self.server.dbs[target]["tables"]["market.contracts"] -= 1
            return output

        with self.assertRaises(rb.VerificationError):
            rb.backup(self.env_file, self.meta, self.root / "backups" / "set1", restore=True,
                      runner=restore_with_loss, clock=Clock(), base_env={})
        evidence = self.evidence()
        self.assertEqual(evidence["status"], "failed")
        self.assertEqual(evidence["failure"]["stage"], "market:compare-restore")
        self.assertIn("market.contracts 1200 -> 1199", evidence["failure"]["differences"][0])
        self.assertFalse(evidence["databases"][0]["restore_verified"])
        self.assert_nothing_deleted()

    def test_source_hash_difference_is_detected(self):
        original = FakeServer.__call__

        def tamper(args, env):
            output = original(self.server, args, env)
            if args[0] == "pg_restore" and "--dbname" in args:
                target = args[args.index("--dbname") + 1]
                if target.startswith("illoura_market_restore"):
                    self.server.dbs[target]["hashes"]["market.snapshots"] = [3, "zzz"]
            return output

        with self.assertRaises(rb.VerificationError):
            rb.backup(self.env_file, self.meta, self.root / "backups" / "set1", restore=True,
                      runner=tamper, clock=Clock(), base_env={})
        self.assertIn("market.snapshots", self.evidence()["failure"]["differences"][0])

    def test_metabase_content_difference_is_detected(self):
        original = FakeServer.__call__

        def tamper(args, env):
            output = original(self.server, args, env)
            if args[0] == "pg_restore" and "--dbname" in args:
                target = args[args.index("--dbname") + 1]
                if target.startswith("illoura_metabase_restore"):
                    self.server.dbs[target]["content"]["report_card"] = [4, "c0"]
            return output

        with self.assertRaises(rb.VerificationError):
            rb.backup(self.env_file, self.meta, self.root / "backups" / "set1", restore=True,
                      runner=tamper, clock=Clock(), base_env={})
        evidence = self.evidence()
        self.assertEqual(evidence["failure"]["stage"], "metabase:compare-restore")
        self.assertTrue(evidence["databases"][0]["restore_verified"])

    def test_live_change_during_dump_stops_before_certifying(self):
        def change_after_dump(server, args, env):
            if args[0] == "pg_dump" and env["PGDATABASE"] == "illoura_market":
                server.dbs["illoura_market"]["tables"]["market.contracts"] += 7

        self.server.hook = change_after_dump
        with self.assertRaises(rb.VerificationError):
            self.run_backup()
        evidence = self.evidence()
        self.assertEqual(evidence["failure"]["stage"], "market:check-unchanged")
        self.assertIn("market.contracts 1200 -> 1207", evidence["failure"]["differences"][0])
        self.assertFalse(any(c[0] == "sudo" for c in self.server.commands))

    def test_command_failure_evidence_is_redacted(self):
        def fail_dump(server, args, env):
            if args[0] == "pg_dump":
                raise rb.CommandError(
                    "pg_dump", 1,
                    f'pg_dump: error: connection to server at "{ADDRESS}", port 5432 failed: '
                    f"password={MARKET_PASSWORD} postgresql://illoura_market_owner:{MARKET_PASSWORD}@{ADDRESS}/x")

        self.server.hook = fail_dump
        with self.assertRaises(rb.CommandError):
            self.run_backup()
        failure = self.evidence()["failure"]  # evidence() asserts no secret/address appears
        self.assertEqual(failure["stage"], "market:dump")
        self.assertEqual(failure["command"], "pg_dump")
        self.assertIn("<redacted>", failure["detail"])
        self.assertIn("<database-url>", failure["detail"])

    def test_interruption_is_recorded_and_retry_uses_a_new_directory(self):
        def stop_during_restore(server, args, env):
            if args[0] == "pg_restore" and "--dbname" in args:
                raise rb.Interrupted("stopped by signal 15")

        self.server.hook = stop_during_restore
        with self.assertRaises(rb.Interrupted):
            self.run_backup("first")
        first = self.evidence("first")
        self.assertEqual(first["status"], "interrupted")
        self.assertEqual(first["failure"]["stage"], "market:restore")
        self.assertIn("finished_at", first)
        first_text = (self.root / "backups" / "first" / "verification.json").read_text()

        self.server.hook = None
        with self.assertRaises(FileExistsError):
            self.run_backup("first")
        self.assertEqual((self.root / "backups" / "first" / "verification.json").read_text(), first_text)
        self.assertEqual(self.run_backup("second")["status"], "complete")
        self.assert_nothing_deleted()

    def test_missing_encryption_key_fails_before_any_dump(self):
        (self.meta / "metabase.env").write_text(f"MB_DB_PASS={APP_PASSWORD}\nMB_ENCRYPTION_SECRET_KEY=\n")
        with self.assertRaises(rb.VerificationError):
            self.run_backup()
        evidence = self.evidence()
        self.assertEqual(evidence["failure"]["stage"], "recovery-files")
        self.assertIn("MB_ENCRYPTION_SECRET_KEY", evidence["failure"]["message"])
        self.assertEqual(self.server.commands, [])

    def test_missing_credentials_file_fails(self):
        (self.meta / "credentials.json").unlink()
        with self.assertRaises(rb.VerificationError):
            self.run_backup()
        self.assertIn("credentials.json", self.evidence()["failure"]["message"])

    def test_password_mismatch_is_reported_as_warning(self):
        (self.meta / "metabase.env").write_text(
            f"MB_DB_PASS=other-value\nMB_ENCRYPTION_SECRET_KEY={ENCRYPTION_KEY}\n")
        self.assertEqual(self.run_backup()["status"], "complete")
        self.assertEqual(len(self.evidence()["warnings"]), 1)

    def test_unexpected_table_identifier_is_refused(self):
        self.server.dbs["illoura_market"]["tables"]['market.x"; DROP TABLE y; --'] = 1
        with self.assertRaises(rb.VerificationError):
            self.run_backup()
        self.assertEqual(self.evidence()["failure"]["stage"], "market:read-live")
        self.assert_nothing_deleted()

    def test_main_reports_failure_without_secrets(self):
        import contextlib
        import io

        out = io.StringIO()
        missing_env = self.root / "nope.env"
        with contextlib.redirect_stdout(out):
            code = rb.main(["--environment", str(missing_env), "--metabase-private", str(self.meta),
                            "--output", str(self.root / "backups" / "m")])
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out.getvalue())["status"], "failed")
        self.assertEqual(self.evidence("m")["status"], "failed")

    def test_redact_handles_hostnames_and_urls(self):
        text = rb.redact(f"host=ip-10-1-2-3.ec2.internal url postgresql+psycopg://u:p@{ADDRESS}/db", ["p"])
        self.assertNotIn(ADDRESS, text)
        self.assertNotIn("ip-10-1-2-3", text)


if __name__ == "__main__":
    unittest.main()
