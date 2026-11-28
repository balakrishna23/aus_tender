"""Research dataset export: SQLite writing, validation and the never-raising root install."""

from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unittest
import uuid

deploy = Path(__file__).resolve().parents[1] / "deploy"
sys.path.insert(0, str(deploy))
spec = importlib.util.spec_from_file_location("research_export", deploy / "illoura_research_export.py")
export = importlib.util.module_from_spec(spec)
spec.loader.exec_module(export)


def build(path, empty=(), skip=()):
    """A complete export written with the module's own writer (one row per table)."""
    db = sqlite3.connect(path)
    counts = {}
    for name in export.TABLES:
        if name in skip:
            continue
        columns = sorted({"id"} | set(export.INDEXES.get(name, [])))
        rows = [] if name in empty else [tuple("x" for _ in columns)]
        counts[name] = export.write_table(db, name, columns, ["TEXT"] * len(columns), iter(rows))
    if skip:
        db.execute("CREATE TABLE export_info (key TEXT PRIMARY KEY, value TEXT)")
        db.commit()
    else:
        export.finish(db, counts, {"exported_at_utc": "2026-11-24T00:00:00+00:00"})
    db.close()
    return counts


class Cells(unittest.TestCase):
    def test_values_become_sqlite_values(self):
        ident = uuid.uuid4()
        self.assertEqual(export.cell(Decimal("12.50")), 12.5)
        self.assertEqual(export.cell(date(2026, 11, 24)), "2026-11-24")
        self.assertEqual(export.cell(datetime(2026, 11, 24, tzinfo=timezone.utc)), "2026-11-24T00:00:00+00:00")
        self.assertEqual(json.loads(export.cell({"b": 1, "a": [1, 2]})), {"a": [1, 2], "b": 1})
        self.assertEqual(export.cell(["x", "y"]), '["x", "y"]')
        self.assertEqual(export.cell(ident), str(ident))
        self.assertIs(export.cell(None), None)
        self.assertEqual(export.cell(True), True)


class WriteAndValidate(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)

    def test_complete_export_validates_with_counts_and_info(self):
        path = self.dir / "research.db"
        build(path)
        counts = export.validate(path)
        self.assertEqual(set(counts), set(export.TABLES))
        self.assertTrue(all(v == 1 for v in counts.values()))
        with closing(sqlite3.connect(path)) as db:
            info = dict(db.execute("SELECT key, value FROM export_info"))
        self.assertEqual(json.loads(info["table_rows"])["contracts"], 1)

    def test_batches_stream_every_row(self):
        db = sqlite3.connect(":memory:")
        n = export.write_table(db, "t", ["a", "b"], ["INTEGER", "TEXT"], ((i, {"k": i}) for i in range(12_345)), batch=1000)
        self.assertEqual(n, 12_345)
        self.assertEqual(db.execute("SELECT count(*), max(a) FROM t").fetchone(), (12_345, 12_344))
        self.assertEqual(json.loads(db.execute("SELECT b FROM t WHERE a=7").fetchone()[0]), {"k": 7})

    def test_missing_table_is_refused(self):
        path = self.dir / "research.db"
        build(path, skip=("remoteness",))
        with self.assertRaisesRegex(ValueError, "lacks tables: remoteness"):
            export.validate(path)

    def test_empty_required_table_is_refused(self):
        path = self.dir / "research.db"
        build(path, empty=("register_entities",))
        with self.assertRaisesRegex(ValueError, "empty tables: register_entities"):
            export.validate(path)

    def test_optional_tables_may_be_empty(self):
        path = self.dir / "research.db"
        build(path, empty=("panel_calendar", "register_identity_conflicts"))
        self.assertEqual(export.validate(path)["panel_calendar"], 0)

    def test_individuals_are_included_and_flagged(self):
        for table in ("register_entities", "register_names"):
            self.assertNotIn("WHERE NOT", export.TABLES[table])
        self.assertIn(export.INDIVIDUAL + " AS is_individual", export.TABLES["register_entities"])
        self.assertIn("is_individual", export.INDIVIDUAL)
        self.assertIn("sole", export.INDIVIDUAL)

    def test_register_scope_is_a_join_not_a_per_row_subquery(self):
        # 29 Nov 2026: `abn IN (SELECT ... UNION ...)` never finished on the national registers (the planner
        # rescanned a materialised list for each of ~13.6M rows); the scope must be computed once and joined.
        for table in ("register_entities", "register_names"):
            query = " ".join(export.TABLES[table].split())
            self.assertTrue(query.startswith("WITH scope AS MATERIALIZED ("), table)
            self.assertIn("LEFT JOIN scope s ON s.abn = e.abn", query)
            self.assertIn("WHERE (e.source IN ('acnc','oric') OR s.abn IS NOT NULL)", query)
            self.assertNotIn("e.abn IN (SELECT", query)

    def test_export_has_no_write_statements(self):
        for name, query in export.TABLES.items():
            words = query.upper().split()
            for verb in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "TRUNCATE", "GRANT"):
                self.assertNotIn(verb, words, name)

    def test_export_refuses_an_existing_destination(self):
        path = self.dir / "research.db"
        path.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "already exists"):
            export.export(self.dir / "unused.env", path)


@unittest.skipIf(os.name == "nt", "install uses POSIX accounts")
class Install(unittest.TestCase):
    def setUp(self):
        import pwd
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.dir = Path(temp.name)
        self.me = pwd.getpwuid(os.getuid()).pw_name
        (self.dir / "data").mkdir()
        (self.dir / "databases").mkdir()
        (self.dir / "scratch").mkdir()
        self.config = self.dir / "refresh.json"
        self.config.write_text(json.dumps({"data_root": str(self.dir / "data"), "build": "b", "tools": "t"}))
        self.config.chmod(0o644)
        self.target = self.dir / "databases/research.db"
        self.calls = []

    def install(self, runner, **kw):
        kw.setdefault("environ", {})
        return export.install(config=self.config, target=self.target, runner=runner, owner=self.me,
                              file_owner=self.me, scratch=self.dir / "scratch", chown=lambda *a: None,
                              trusted_uid=os.getuid(), **kw)

    def writer(self, **build_kw):
        def run(command, timeout):
            self.calls.append((command, timeout))
            build(Path(command[command.index("--destination") + 1]), **build_kw)
            return "{}"
        return run

    def test_success_replaces_the_file_and_cleans_up(self):
        self.target.write_text("old")
        result = self.install(self.writer())
        self.assertEqual(result["status"], "updated")
        self.assertEqual(export.validate(self.target)["contracts"], 1)
        self.assertEqual(list((self.dir / "scratch").iterdir()), [])
        self.assertEqual([p.name for p in (self.dir / "databases").iterdir()], ["research.db"])
        command = [str(c) for c in self.calls[0][0]]
        self.assertEqual(command[:4], ["runuser", "-u", self.me, "--"])
        self.assertIn("export", command)
        self.assertEqual(oct(self.target.stat().st_mode & 0o777), "0o640")

    def test_failed_export_keeps_the_previous_file(self):
        self.target.write_text("old")

        def broken(command, timeout):
            raise RuntimeError("Research export step failed")
        self.assertEqual(self.install(broken)["status"], "failed")
        self.assertEqual(self.target.read_text(), "old")
        self.assertEqual(list((self.dir / "scratch").iterdir()), [])

    def test_invalid_export_keeps_the_previous_file(self):
        self.target.write_text("old")
        result = self.install(self.writer(empty=("contracts",)))
        self.assertEqual(result, {"status": "failed", "error_code": "ValueError", "seconds": result["seconds"]})
        self.assertEqual(self.target.read_text(), "old")
        self.assertEqual([p.name for p in (self.dir / "databases").iterdir()], ["research.db"])

    def test_missing_configuration_is_disabled(self):
        self.config.unlink()
        self.assertEqual(self.install(self.writer())["status"], "disabled")
        self.assertEqual(self.calls, [])

    def test_writable_configuration_is_refused(self):
        self.config.chmod(0o666)
        self.assertEqual(self.install(self.writer())["status"], "failed")
        self.assertEqual(self.calls, [])

    def test_close_morning_deadline_skips_without_running(self):
        soon = str(time.time() + 600)
        self.assertEqual(self.install(self.writer(), environ={"ILLOURA_MORNING_DEADLINE": soon})["status"], "skipped")
        self.assertEqual(self.calls, [])
        later = str(time.time() + 4 * 3600)
        self.assertEqual(self.install(self.writer(), environ={"ILLOURA_MORNING_DEADLINE": later})["status"], "updated")


class CoordinatorHook(unittest.TestCase):
    def test_collect_refreshes_research_before_publication_and_never_raises(self):
        source = (deploy / "illoura_cloud_collect.py").read_text(encoding="utf-8")
        hook = 'state["research_dataset"] = refresh_research_dataset()'
        self.assertLess(source.index(hook), source.index("publication = publish("))
        self.assertIn("from illoura_research_export import install", source)


@unittest.skipUnless((deploy.parent / "api/app.py").exists(), "API source not staged")
class ApiPolicy(unittest.TestCase):
    def test_api_allows_exactly_the_exported_tables(self):
        import ast
        tree = ast.parse((deploy.parent / "api/app.py").read_text(encoding="utf-8"))
        allowed = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                       and any(isinstance(t, ast.Name) and t.id == "ALLOWED" for t in n.targets))
        self.assertEqual(set(allowed["research"]), export.EXPECTED)
        self.assertEqual(len(allowed["research"]), len(export.EXPECTED))


if __name__ == "__main__":
    unittest.main()
