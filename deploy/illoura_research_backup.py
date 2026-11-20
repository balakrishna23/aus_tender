"""Private recovery set for the research and Metabase PostgreSQL databases.

Creates a NEW owner-only directory holding a custom-format dump of each database,
SHA256SUMS, copies of the private Metabase recovery files (application database
credentials and the encryption key needed to read the restored application database)
and verification.json. With --restore-drill each dump is restored into a new, isolated
database and compared with the live database it came from:

- research: exact row count of every table and populated materialized view in every
  application schema, every source-snapshot hash (any table with id + sha256 columns)
  and the schema revision;
- Metabase: saved questions, dashboards, dashboard cards, collections, database
  connections and users (stable columns only; usage counters are ignored).

Each live database is read before and after its dump; if it changed while being dumped
the run stops instead of certifying an inconsistent copy.

verification.json is rewritten at every stage, so a failed, interrupted (SIGTERM, SIGHUP,
Ctrl-C) or killed run still shows how far it got. Nothing is ever deleted: earlier
recovery sets, dumps and restore databases are retained. No scheduler, service or
container changes. The directory contains recovery secrets: keep it owner-only and out
of public reports. Output and evidence never contain passwords, keys or addresses.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
from datetime import UTC, datetime
from functools import partial
from pathlib import Path
from urllib.parse import unquote, urlsplit

IDENTIFIER = re.compile(r"[a-z_][a-z0-9_]*")
METABASE_DATABASE = "illoura_metabase"
METABASE_USER = "illoura_metabase_app"

# File -> keys that must be present and non-empty for the recovery set to be usable.
REQUIRED_RECOVERY_FILES = {
    "credentials.json": ("app_password",),
    "metabase.env": ("MB_ENCRYPTION_SECRET_KEY", "MB_DB_PASS"),
}
OPTIONAL_RECOVERY_FILES = ("provisioned.json",)

TABLES_SQL = (
    "SELECT schemaname, tablename FROM pg_tables "
    "WHERE schemaname <> 'information_schema' AND left(schemaname, 3) <> 'pg_' ORDER BY 1, 2"
)
MATVIEWS_SQL = (
    "SELECT schemaname, matviewname FROM pg_matviews "
    "WHERE ispopulated AND schemaname <> 'information_schema' AND left(schemaname, 3) <> 'pg_' "
    "ORDER BY 1, 2"
)
HASHED_TABLES_SQL = (
    "SELECT h.schemaname, h.tablename FROM pg_tables h "
    "WHERE h.schemaname <> 'information_schema' AND left(h.schemaname, 3) <> 'pg_' "
    "AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = h.schemaname "
    "AND c.table_name = h.tablename AND c.column_name = 'sha256') "
    "AND EXISTS (SELECT 1 FROM information_schema.columns c WHERE c.table_schema = h.schemaname "
    "AND c.table_name = h.tablename AND c.column_name = 'id') ORDER BY 1, 2"
)

# Metabase application content compared after a restore. Only columns that change when
# someone edits content are used; view counters and last-used timestamps are left out
# because Metabase updates them while it runs.
METABASE_CONTENT = {
    "report_card": (
        "id", "entity_id", "name", "type", "archived", "display",
        "database_id", "collection_id", "dataset_query",
    ),
    "report_dashboard": ("id", "entity_id", "name", "archived", "collection_id"),
    "report_dashboardcard": ("id", "dashboard_id", "card_id", "row", "col", "size_x", "size_y"),
    "collection": ("id", "entity_id", "name", "archived", "location"),
    "metabase_database": ("id", "name", "engine"),
    "core_user": ("id", "is_active", "is_superuser"),
}
METABASE_REQUIRED_CONTENT = ("report_card", "report_dashboard")
METABASE_COLUMNS_SQL = (
    "SELECT table_name, column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name IN ("
    + ", ".join(f"'{name}'" for name in METABASE_CONTENT)
    + ") ORDER BY 1, 2"
)

RETENTION_NOTE = (
    "Nothing deleted: dumps, restore databases, earlier recovery sets and live databases are retained"
)


class VerificationError(RuntimeError):
    """A check failed; ``differences`` lists what differed (names and counts only)."""

    def __init__(self, message, differences=None):
        super().__init__(message)
        self.differences = list(differences or [])[:25]


class CommandError(RuntimeError):
    def __init__(self, command, returncode, stderr=""):
        super().__init__(f"{command} exited with status {returncode}")
        self.command = command
        self.returncode = returncode
        self.stderr = stderr or ""


class Interrupted(Exception):
    """Raised from a signal handler so the evidence is written before exiting."""


_REDACTIONS = (
    (re.compile(r"postgres(?:ql)?(?:\+\w+)?://\S+", re.IGNORECASE), "<database-url>"),
    (re.compile(r"\bip-\d+-\d+-\d+-\d+\S*", re.IGNORECASE), "<address>"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "<address>"),
    (re.compile(r"\b[0-9a-f]{1,4}(?::[0-9a-f]{0,4}){2,7}\b", re.IGNORECASE), "<address>"),
    (re.compile(r"(?i)\b(password|passwd|secret|token|key|host)\b\s*[=:]\s*\S+"), r"\1=<redacted>"),
)


def redact(text, secrets=()):
    text = str(text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def run_command(args, env, timeout=3600):
    """Run one command; raise CommandError (with stderr kept for redacted evidence)."""
    label = next((a for a in args if a in ("pg_dump", "pg_restore", "psql")), args[0])
    try:
        result = subprocess.run(args, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise CommandError(label, "timeout", f"no result after {timeout} seconds") from exc
    if result.returncode:
        raise CommandError(label, result.returncode, result.stderr)
    return result.stdout


def read_env(path):
    values = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _check_identifier(value, what):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or len(value) > 63:
        raise VerificationError(f"Unexpected {what} identifier")
    return value


def _relations(rows):
    names = []
    for row in rows:
        if len(row) != 2:
            raise VerificationError("Unexpected catalogue output")
        names.append((_check_identifier(row[0], "schema"), _check_identifier(row[1], "table")))
    return names


def _count_sql(relations):
    return (
        " UNION ALL ".join(f"SELECT '{s}.{t}', count(*) FROM \"{s}\".\"{t}\"" for s, t in relations)
        + " ORDER BY 1"
    )


def _hash_sql(relations):
    parts = [
        f"SELECT '{s}.{t}', count(*), coalesce(md5(string_agg(id::text || ':' || coalesce(sha256::text, ''), "
        f"',' ORDER BY id::text, sha256::text)), '') FROM \"{s}\".\"{t}\""
        for s, t in relations
    ]
    return " UNION ALL ".join(parts) + " ORDER BY 1"


def _content_sql(table, columns):
    fields = ", ".join(f"coalesce(\"{c}\"::text, '~null~')" for c in columns)
    return (
        f"SELECT '{table}', count(*), coalesce(md5(string_agg(concat_ws('|', {fields}), "
        f"E'\\n' ORDER BY \"id\"::text)), '') FROM public.\"{table}\""
    )


def market_signature(query):
    """Exact counts, source hashes and schema revision of the research database."""
    tables = _relations(query(TABLES_SQL))
    if not tables:
        raise VerificationError("The research database has no application tables")
    matviews = _relations(query(MATVIEWS_SQL))
    hashed = _relations(query(HASHED_TABLES_SQL))
    counts = {row[0]: int(row[1]) for row in query(_count_sql(tables + matviews))}
    hashes = {row[0]: [int(row[1]), row[2]] for row in query(_hash_sql(hashed))} if hashed else {}
    revision = sorted(
        row[0]
        for schema, table in tables
        if table == "alembic_version"
        for row in query(f'SELECT version_num FROM "{schema}"."alembic_version" ORDER BY 1')
    )
    return {"relations": counts, "source_hashes": hashes, "schema_revision": revision}


def metabase_signature(query):
    """Counts and stable-column hashes of Metabase's saved content."""
    columns = {}
    for row in query(METABASE_COLUMNS_SQL):
        columns.setdefault(row[0], set()).add(row[1])
    missing = [name for name in METABASE_REQUIRED_CONTENT if name not in columns]
    if missing:
        raise VerificationError("Metabase content tables missing: " + ", ".join(missing))
    parts = []
    for table, wanted in METABASE_CONTENT.items():
        if table not in columns:
            continue
        present = [c for c in wanted if c in columns[table]]
        if "id" not in present:
            raise VerificationError(f"Metabase table {table} has no id column")
        parts.append(_content_sql(table, present))
    rows = query(" UNION ALL ".join(parts) + " ORDER BY 1")
    return {"content": {row[0]: [int(row[1]), row[2]] for row in rows}}


def summarize(label, signature):
    if label == "market":
        relations = signature["relations"]
        hashes = signature["source_hashes"]
        return {
            "schema_revision": signature["schema_revision"],
            "relations_counted": len(relations),
            "rows_counted": sum(relations.values()),
            "source_hash_tables": len(hashes),
            "source_hash_rows": sum(count for count, _ in hashes.values()),
        }
    return {"content_rows": {name: value[0] for name, value in signature["content"].items()}}


def differences(expected, actual):
    found = []
    for section in sorted(set(expected) | set(actual)):
        left, right = expected.get(section), actual.get(section)
        if isinstance(left, dict) and isinstance(right, dict):
            for key in sorted(set(left) | set(right)):
                if left.get(key) != right.get(key):
                    found.append(f"{section}: {key} {_short(left.get(key))} -> {_short(right.get(key))}")
        elif left != right:
            found.append(f"{section}: {_short(left)} -> {_short(right)}")
    return found


def _short(value):
    if value is None:
        return "missing"
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], int):
        return f"{value[0]} rows (hash {str(value[1])[:8]})"
    return str(value)


class Evidence:
    """verification.json, rewritten atomically after every stage."""

    def __init__(self, path, clock):
        self.path = Path(path)
        self.clock = clock
        self.data = {}

    def save(self):
        self.data["updated_at"] = self.clock().isoformat()
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self.data, indent=2))
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, self.path)

    def stage(self, name):
        self.data["stage"] = name
        self.save()

    def finish(self, status, **extra):
        self.data.update(status=status, finished_at=self.clock().isoformat(), **extra)
        self.save()


def _copy_recovery_files(source_dir, destination, evidence):
    evidence.stage("recovery-files")
    copied, parsed = [], {}
    for name in [*REQUIRED_RECOVERY_FILES, *OPTIONAL_RECOVERY_FILES]:
        source = source_dir / name
        required = name in REQUIRED_RECOVERY_FILES
        if not source.is_file():
            if required:
                raise VerificationError(f"Required recovery file missing: {name}")
            continue
        if required:
            values = json.loads(source.read_text()) if name.endswith(".json") else read_env(source)
            empty = [key for key in REQUIRED_RECOVERY_FILES[name] if not str(values.get(key) or "").strip()]
            if empty:
                raise VerificationError(f"{name} lacks required recovery value(s): " + ", ".join(empty))
            parsed[name] = values
        target = destination / name
        shutil.copyfile(source, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        if sha256_file(source) != sha256_file(target):
            raise VerificationError(f"Copied recovery file does not match its source: {name}")
        copied.append({"name": name, "required": required, "bytes": target.stat().st_size, "verified": True})
    evidence.data["recovery_files"] = copied
    if parsed["credentials.json"]["app_password"] != parsed["metabase.env"]["MB_DB_PASS"]:
        evidence.data.setdefault("warnings", []).append(
            "credentials.json and metabase.env hold different Metabase application database passwords"
        )
    evidence.save()
    return parsed


def _back_up_database(target, destination, restore, stamp, runner, base_env, evidence):
    label, database, user, env, signature = (
        target["label"], target["database"], target["user"], target["env"], target["signature"]
    )

    def query(sql, db=database):
        output = runner(["psql", "-X", "-At", "-v", "ON_ERROR_STOP=1", "-c", sql], dict(env, PGDATABASE=db))
        return [line.split("|") for line in output.splitlines() if line]

    item = {"label": label, "database": database, "dump_file": f"{label}.dump"}
    evidence.data["databases"].append(item)

    evidence.stage(f"{label}:read-live")
    before = signature(query)
    item["live"] = summarize(label, before)

    evidence.stage(f"{label}:dump")
    dump = destination / item["dump_file"]
    runner(["pg_dump", "--format=custom", "--no-owner", "--no-acl", "--file", str(dump)], dict(env, PGDATABASE=database))

    evidence.stage(f"{label}:check-unchanged")
    after = signature(query)
    if after != before:
        raise VerificationError(
            f"The {label} database changed while it was being dumped; rerun outside the refresh/import window",
            differences(before, after),
        )

    evidence.stage(f"{label}:check-dump")
    runner(["pg_restore", "--list", str(dump)], dict(env, PGDATABASE=database))
    item.update(bytes=dump.stat().st_size, sha256=sha256_file(dump))
    evidence.save()

    if not restore:
        return item
    restored = _check_identifier(f"{database}_restore_{stamp}", "restore database")
    item.update(restored_database=restored, restore_verified=False)
    evidence.stage(f"{label}:create-restore-database")
    runner(
        ["sudo", "-u", "postgres", "psql", "-X", "-v", "ON_ERROR_STOP=1", "-c",
         f"CREATE DATABASE {restored} OWNER {user}"],
        base_env,
    )
    evidence.stage(f"{label}:restore")
    runner(
        ["pg_restore", "--exit-on-error", "--no-owner", "--no-acl", "--dbname", restored, str(dump)],
        dict(env, PGDATABASE=restored),
    )
    evidence.stage(f"{label}:compare-restore")
    restored_signature = signature(partial(query, db=restored))
    if restored_signature != before:
        raise VerificationError(
            f"The restored {label} database differs from the live database",
            differences(before, restored_signature),
        )
    if sha256_file(dump) != item["sha256"]:
        raise VerificationError(f"The {label} dump file changed during the restore drill")
    item["restore_verified"] = True
    item["verified"] = (
        "Exact counts of every table and populated materialized view, every source-snapshot hash and the schema revision match"
        if label == "market"
        else "Saved questions, dashboards, dashboard cards, collections, connections and users match"
    )
    evidence.save()
    return item


def backup(environment, metabase_private, destination, restore=False, runner=None,
           clock=None, command_timeout=3600, base_env=None):
    clock = clock or (lambda: datetime.now(UTC))
    runner = runner or partial(run_command, timeout=command_timeout)
    base_env = dict(os.environ if base_env is None else base_env)
    os.umask(0o077)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False, mode=0o700)  # never reuse an existing set
    started = clock()
    stamp = started.strftime("%Y%m%dt%H%M%Sz")
    evidence = Evidence(destination / "verification.json", clock)
    evidence.data.update(
        status="running", stage="start", started_at=started.isoformat(), destination=str(destination),
        restore_drill=bool(restore), databases=[], contains_recovery_secrets=True,
        public_distribution_allowed=False, retention=RETENTION_NOTE,
    )
    evidence.save()
    secrets = []
    try:
        values = read_env(environment)
        url = urlsplit(values["ILLOURA_MARKET_DATABASE_URL"].replace("postgresql+psycopg", "postgresql"))
        market_user, market_password = unquote(url.username or ""), unquote(url.password or "")
        secrets += [market_password, url.hostname or ""]
        meta_dir = Path(metabase_private)
        recovery = _copy_recovery_files(meta_dir, destination, evidence)
        secrets += [str(v) for values_ in recovery.values() for v in values_.values() if isinstance(v, str)]
        common = dict(base_env, PGHOST=url.hostname or "", PGPORT=str(url.port or 5432))
        targets = [
            {"label": "market", "database": url.path.lstrip("/"), "user": market_user,
             "password": market_password, "signature": market_signature},
            {"label": "metabase", "database": METABASE_DATABASE, "user": METABASE_USER,
             "password": recovery["credentials.json"]["app_password"], "signature": metabase_signature},
        ]
        for target in targets:
            _check_identifier(target["database"], "database")
            _check_identifier(target["user"], "role")
            target["env"] = dict(common, PGUSER=target["user"], PGPASSWORD=target["password"])
        for target in targets:
            _back_up_database(target, destination, restore, stamp, runner, base_env, evidence)
        evidence.stage("checksums")
        lines = [f"{item['sha256']}  {item['dump_file']}" for item in evidence.data["databases"]]
        lines += [f"{sha256_file(destination / r['name'])}  {r['name']}" for r in evidence.data["recovery_files"]]
        (destination / "SHA256SUMS").write_text("\n".join(lines) + "\n")
        evidence.finish("complete", stage="done", checksums_file="SHA256SUMS")
        return evidence.data
    except (Interrupted, KeyboardInterrupt) as exc:
        evidence.finish("interrupted", failure=_failure(evidence, exc, secrets))
        raise
    except Exception as exc:
        evidence.finish("failed", failure=_failure(evidence, exc, secrets))
        raise


def _failure(evidence, exc, secrets):
    failure = {"stage": evidence.data.get("stage"), "error_code": type(exc).__name__,
               "message": redact(exc, secrets)[:300]}
    if isinstance(exc, CommandError):
        failure["command"] = exc.command
        failure["detail"] = redact(exc.stderr.strip()[-400:], secrets)
    if isinstance(exc, VerificationError) and exc.differences:
        failure["differences"] = [redact(d, secrets) for d in exc.differences]
    return failure


def _raise_interrupted(signum, frame):
    raise Interrupted(f"stopped by signal {signum}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--environment", required=True, help="private env file with ILLOURA_MARKET_DATABASE_URL")
    p.add_argument("--metabase-private", required=True, help="private Metabase directory")
    p.add_argument("--output", required=True, help="NEW directory for this recovery set")
    p.add_argument("--restore-drill", action="store_true", help="restore into new isolated databases and compare")
    p.add_argument("--command-timeout", type=int, default=3600, help="seconds per pg_dump/pg_restore/psql call")
    args = p.parse_args(argv)
    for name in ("SIGTERM", "SIGHUP"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), _raise_interrupted)
    evidence_path = str(Path(args.output) / "verification.json")
    try:
        result = backup(args.environment, args.metabase_private, args.output, args.restore_drill,
                        command_timeout=args.command_timeout)
    except FileExistsError:
        print(json.dumps({"status": "refused", "reason": "output directory already exists; choose a new one"}))
        return 2
    except BaseException as exc:  # noqa: BLE001 - evidence file already records the details
        status = "interrupted" if isinstance(exc, (Interrupted, KeyboardInterrupt)) else "failed"
        print(json.dumps({"status": status, "error_code": type(exc).__name__, "evidence": evidence_path}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
