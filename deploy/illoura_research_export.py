"""Research dataset for the read API: a SQLite export of the current research state.

Two halves:
  export   runs as the research owner (ubuntu) with the research interpreter. One REPEATABLE READ,
           READ ONLY transaction, so every table comes from the same database moment. Writes a new
           SQLite file and nothing else.
  install  runs as root (from the collection coordinator before publication, or by hand). Runs
           `export` unprivileged into a private temporary folder, validates the file, then atomically
           replaces /opt/aus_tender/databases/research.db. Any failure leaves the previous file in
           place, so publication always finds a complete research.db once one exists. Never raises.

Individuals (sole traders and other people) in the business registers are included since 25 Nov 2026
(Sirish's instruction) and flagged in register_entities.is_individual. Raw history
(every past snapshot) stays in PostgreSQL; this file holds the current state only.
"""
import argparse
from contextlib import closing
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import time
import uuid

CONFIG = Path("/etc/illoura-market/refresh.json")
TARGET = Path("/opt/aus_tender/databases/research.db")
EXPORT_TIMEOUT_SECONDS = 300
# Leave this much of the morning budget untouched for the brief itself.
MORNING_RESERVE_SECONDS = 900

# Marks a register record that is a person; used for the flag and count, never as a filter.
INDIVIDUAL = (
    "(coalesce(e.is_individual, false) OR coalesce(e.entity_type,'') ~* "
    "'^IND$|individual|sole[[:space:]]*(trader|proprietor)')"
)
PROCUREMENT = "(SELECT snapshot_id FROM market.current_procurement_snapshot)"
# The national ABN/ASIC registers hold millions of rows (migration 024); the API copy keeps the rows research
# uses: contract buyers/suppliers, aged-care provider matches and the complete ACNC and ORIC registers.
REGISTER_SCOPE = """(e.source IN ('acnc','oric')
        OR e.abn IN (SELECT supplier_abn FROM market.current_contracts WHERE supplier_abn IS NOT NULL
                     UNION SELECT buyer_abn FROM market.current_contracts WHERE buyer_abn IS NOT NULL
                     UNION SELECT abn FROM market.v_aged_care_provider_proposals WHERE abn IS NOT NULL))"""

# Table in research.db -> query. api/app.py ALLOWED["research"] lists exactly these plus export_info.
TABLES = {
    "contracts": """SELECT snapshot_id, cn_id, buyer_key, buyer, buyer_abn, supplier, supplier_abn,
        category_family, original_value, current_value, start_date, current_end_date, first_published,
        source_url, retrieved_at, issues FROM market.current_contracts""",
    "register_entities": """SELECT e.source, e.effective_date, e.scope_kind, e.snapshot_id, e.record_id,
        e.abn, e.acn, e.icn, e.name, e.name_key, """ + INDIVIDUAL + """ AS is_individual, e.status,
        e.status_from, e.registered_on, e.deregistered_on, e.entity_type, e.state, e.postcode,
        e.attributes, e.issues
        FROM market.current_register_entities e WHERE """ + REGISTER_SCOPE,
    "register_names": """SELECT e.source, n.snapshot_id, n.record_id, n.name_type, n.name, n.name_key
        FROM market.register_names n JOIN market.current_register_entities e
          ON e.snapshot_id = n.snapshot_id AND e.record_id = n.record_id WHERE """ + REGISTER_SCOPE,
    # Migration 024: publicly listed ICT / digital / workforce / procurement role holders and agency heads.
    "agency_org_map": "SELECT * FROM market.v_agency_org_map",
    "organisations": "SELECT * FROM market.organisations",
    "organisation_observations": "SELECT * FROM market.organisation_observations",
    "directory_edges": "SELECT * FROM market.directory_edges",
    "agency_unit_map": "SELECT * FROM market.agency_unit_map",
    "buyer_resolution": "SELECT * FROM market.buyer_resolution",
    "register_identity_conflicts": "SELECT * FROM market.register_identity_conflicts",
    "aps_agencies": "SELECT * FROM market.current_aps_agencies",
    "demand": "SELECT * FROM market.current_demand",
    "demand_signals": "SELECT * FROM market.v_demand_signals",
    "forward": "SELECT * FROM market.current_forward",
    "geography": """SELECT snapshot_id, code, name, state_code, properties, layer, edition, source_url,
        licence, retrieved_at FROM market.current_geography""",
    "remoteness": """SELECT r.snapshot_id, r.layer, r.code, r.payload FROM market.remoteness_rows r
        WHERE r.snapshot_id = (SELECT id FROM market.remoteness_snapshots
                               ORDER BY retrieved_at DESC, imported_at DESC LIMIT 1)""",
    "research_records": """SELECT source, snapshot_id, record_key, payload, observed_at
        FROM market.current_research""",
    "agency_spend": "SELECT * FROM market.v_agency_spend",
    "supplier_profile": "SELECT * FROM market.v_supplier_profile",
    "healthcare_accounts": "SELECT * FROM market.v_healthcare_accounts",
    "panel_calendar": "SELECT * FROM market.v_panel_calendar",
    "panel_watchlist": """SELECT son_id, panel_name, jurisdiction, owner_agency, portal, categories,
        start_date, current_expiry, extension_options, max_possible_expiry, open_for_new_suppliers,
        next_refresh_or_intake_date, refresh_evidence_url, how_to_join, eligibility_requirements,
        illoura_status, marked_relevant, spend_through_panel_12m, contracts_36m, notes, source_url,
        last_verified_at, needs_manual_check, seeded_from, seeded_at, updated_at
        FROM market.panel_watchlist""",
    "panels": "SELECT * FROM market.procurement_panels WHERE snapshot_id = " + PROCUREMENT,
    "panel_links": "SELECT * FROM market.procurement_panel_links WHERE snapshot_id = " + PROCUREMENT,
    "illoura_profile": "SELECT key, value, evidence_url, updated_at, note FROM market.illoura_profile",
    # Migrations 022 and 023 (deploy them before this export runs).
    "aged_care_quality": "SELECT * FROM market.v_aged_care_quality",
    "aged_care_provider_matches": "SELECT * FROM market.v_aged_care_provider_proposals",
    "research_freshness": "SELECT * FROM market.v_research_freshness",
    "account_score": "SELECT * FROM market.v_account_score ORDER BY rank LIMIT 10000",
    "buyict_buyers": "SELECT * FROM market.v_buyict_buyers ORDER BY rank LIMIT 10000",
    "buyict_gaps": "SELECT * FROM market.v_buyict_gaps ORDER BY rank LIMIT 10000",
    "ipp_gap": "SELECT * FROM market.v_ipp_gap ORDER BY rank LIMIT 10000",
    "ipp_levers": "SELECT * FROM market.v_ipp_levers ORDER BY rank LIMIT 10000",
    "prime_targets": "SELECT * FROM market.v_prime_targets ORDER BY rank LIMIT 10000",
    "partner_candidates": "SELECT * FROM market.v_partner_candidates ORDER BY rank LIMIT 10000",
    "indigenous_partners": "SELECT * FROM market.v_indigenous_partners ORDER BY rank LIMIT 10000",
    "rap_document_findings": "SELECT * FROM market.current_rap_document_findings ORDER BY organisation,record_key LIMIT 10000",
    "rap_commitments": """SELECT c.*,d.organisation FROM market.rap_commitments c
        JOIN market.rap_documents d ON d.sha256=c.document_sha256 ORDER BY d.organisation,c.record_key,c.page,c.id LIMIT 100000""",
    "rap_people": """SELECT p.*,d.organisation FROM market.rap_people p
        JOIN market.rap_documents d ON d.sha256=p.document_sha256 ORDER BY d.organisation,p.record_key,p.page,p.id LIMIT 100000""",
    "rap_targets": "SELECT * FROM market.v_rap_targets ORDER BY rank LIMIT 10000",
    "panel_access": "SELECT * FROM market.v_panel_access ORDER BY rank LIMIT 10000",
    "forward_pipeline": "SELECT * FROM market.v_forward_pipeline ORDER BY rank LIMIT 10000",
    "demand_vs_bench": "SELECT * FROM market.v_demand_vs_bench ORDER BY rank LIMIT 10000",
    "lookalikes": "SELECT * FROM market.v_lookalikes ORDER BY rank LIMIT 10000",
    "whats_changed": "SELECT * FROM market.v_whats_changed ORDER BY rank LIMIT 10000",
    "rate_benchmarks": "SELECT * FROM market.v_rate_benchmarks ORDER BY buyer_key,role_category,state NULLS LAST LIMIT 10000",
    "sources": """
        SELECT 'register' AS kind, s.source, s.id AS snapshot_id, s.source_url, s.licence,
               s.retrieved_at, s.effective_date::text AS effective_date, s.imported_at, s.row_count
          FROM market.register_snapshots s JOIN market.current_register_snapshots c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'federal', s.source, s.id, s.source_url, s.licence, s.retrieved_at,
               s.effective_date::text, s.imported_at, s.row_count
          FROM market.snapshots s JOIN market.current_snapshots c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'demand', s.source, s.id, s.source_url, s.licence, s.retrieved_at,
               s.period_end::text, s.imported_at, s.row_count
          FROM market.demand_snapshots s JOIN market.current_demand_snapshots c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'aps', 'aps_employment', s.id, s.source_url, s.licence, s.retrieved_at,
               s.as_at::text, s.imported_at, s.row_count
          FROM market.aps_snapshots s JOIN market.current_aps_snapshot c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'research', s.source, s.id, s.provenance->>'source_url', s.provenance->>'licence',
               s.observed_at, NULL, s.imported_at, s.row_count
          FROM market.research_snapshots s JOIN market.current_research_snapshots c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'geography', s.layer, s.id, s.source_url, s.licence, s.retrieved_at,
               s.edition, s.imported_at, s.row_count
          FROM market.geography_snapshots s JOIN market.current_geography_snapshots c ON c.snapshot_id = s.id
        UNION ALL
        SELECT 'procurement', 'procurement', s.id, s.source_url, s.licence, s.exported_at,
               NULL, s.imported_at, NULL
          FROM market.procurement_snapshots s WHERE s.id = """ + PROCUREMENT + """
        UNION ALL
        SELECT 'forward', 'tender_feed', s.id, NULL, 'Derived public procurement evidence',
               s.exported_at, NULL, s.imported_at, s.row_count
          FROM market.forward_snapshots s JOIN market.current_forward_snapshot c ON c.snapshot_id=s.id
        UNION ALL
        (SELECT 'remoteness', s.edition_chain, s.id, NULL, NULL, s.retrieved_at, NULL, s.imported_at, NULL
           FROM market.remoteness_snapshots s ORDER BY s.retrieved_at DESC, s.imported_at DESC LIMIT 1)""",
}
INDEXES = {
    "account_score": ["lane", "account_key", "rank_in_lane"],
    "lookalikes": ["lane", "account_key"],
    "buyict_gaps": ["buyer_key"],
    "rate_benchmarks": ["buyer_key", "role_category"],
    "contracts": ["buyer_key", "supplier_abn", "cn_id"],
    "register_entities": ["abn", "name_key", "record_id"],
    "register_names": ["record_id", "name_key"],
    "organisations": ["organisation_id", "abn"],
    "research_records": ["source"],
    "demand": ["occupation_code"],
    "panel_links": ["cn_id", "son_id"],
    "supplier_profile": ["supplier_abn"],
    "agency_spend": ["buyer_key"],
}
# Tables that must never be empty in a file that replaces a good one.
REQUIRED_ROWS = ("contracts", "register_entities", "research_records", "organisations", "sources")
EXPECTED = set(TABLES) | {"export_info"}

# PostgreSQL type OIDs -> SQLite column affinity; anything else is stored as TEXT.
AFFINITY = {16: "INTEGER", 20: "INTEGER", 21: "INTEGER", 23: "INTEGER", 26: "INTEGER",
            700: "REAL", 701: "REAL", 1700: "REAL"}


def cell(value):
    """One PostgreSQL value as a SQLite value. JSON stays JSON text so json_extract works."""
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return str(value)


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def write_table(db, name, columns, types, rows, batch=5000):
    """Create one table and stream rows into it. Returns the row count."""
    db.execute("CREATE TABLE " + quoted(name) + " ("
               + ", ".join(quoted(c) + " " + t for c, t in zip(columns, types)) + ")")
    insert = ("INSERT INTO " + quoted(name) + " VALUES ("
              + ", ".join("?" * len(columns)) + ")")
    count, chunk = 0, []
    for row in rows:
        chunk.append(tuple(cell(v) for v in row))
        if len(chunk) >= batch:
            db.executemany(insert, chunk)
            count += len(chunk)
            chunk = []
    if chunk:
        db.executemany(insert, chunk)
        count += len(chunk)
    return count


def finish(db, counts, info):
    """Indexes, the export_info table and an integrity check."""
    for table, columns in INDEXES.items():
        for column in columns:
            db.execute("CREATE INDEX " + quoted("ix_" + table + "_" + column)
                       + " ON " + quoted(table) + " (" + quoted(column) + ")")
    db.execute("CREATE TABLE export_info (key TEXT PRIMARY KEY, value TEXT)")
    info = dict(info, table_rows=counts)
    db.executemany("INSERT INTO export_info VALUES (?, ?)",
                   [(k, v if isinstance(v, str) else json.dumps(v, sort_keys=True)) for k, v in info.items()])
    db.commit()
    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise ValueError("Research export failed its integrity check")


def database_url(environment):
    values = dict(line.split("=", 1) for line in Path(environment).read_text().splitlines()
                  if "=" in line and not line.startswith("#"))
    return values["ILLOURA_MARKET_DATABASE_URL"].strip()


def export(environment, destination):
    """Research owner side. Needs SQLAlchemy and a PostgreSQL driver (the research interpreter)."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError("Export destination already exists")
    import sqlalchemy as sa

    started = time.monotonic()
    engine = sa.create_engine(database_url(environment), pool_pre_ping=False)
    db = sqlite3.connect(destination)
    db.execute("PRAGMA journal_mode=OFF")
    db.execute("PRAGMA synchronous=OFF")
    counts = {}
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
                conn.exec_driver_sql("SET LOCAL statement_timeout = '600s'")
                revision = conn.exec_driver_sql("SELECT version_num FROM public.alembic_version").scalar()
                individuals = conn.exec_driver_sql(
                    "SELECT count(*) FROM market.current_register_entities e WHERE " + INDIVIDUAL).scalar()
                for name, query in TABLES.items():
                    result = conn.execution_options(stream_results=True, yield_per=5000).execute(sa.text(query))
                    description = getattr(result.cursor, "description", None) or []
                    columns = list(result.keys())
                    types = [AFFINITY.get(getattr(d, "type_code", None), "TEXT") for d in description]
                    types += ["TEXT"] * (len(columns) - len(types))
                    counts[name] = write_table(db, name, columns, types, result)
        finish(db, counts, {
            "exported_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "schema_revision": revision,
            "individual_register_records_included": individuals,
            "scope": "Current state of the Illoura research database (one consistent read). "
                     "Raw history is not included. Individuals in business registers are included and "
                     "flagged in register_entities.is_individual.",
            "overlap": "contracts repeats contract_api for joins inside this dataset; do not add the two counts.",
        })
    finally:
        db.close()
        engine.dispose()
    return {"status": "exported", "tables": counts, "seconds": round(time.monotonic() - started, 1)}


def validate(path):
    """Row counts of a finished export; raises if it is incomplete or broken."""
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise ValueError("Research export failed its integrity check")
        present = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not EXPECTED <= present:
            raise ValueError("Research export lacks tables: " + ", ".join(sorted(EXPECTED - present)))
        counts = {t: db.execute("SELECT count(*) FROM " + quoted(t)).fetchone()[0] for t in sorted(TABLES)}
    empty = [t for t in REQUIRED_ROWS if not counts[t]]
    if empty:
        raise ValueError("Research export has empty tables: " + ", ".join(empty))
    return counts


def bounded(command, timeout):
    process = subprocess.Popen([str(c) for c in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        out, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate()
        raise
    if process.returncode:
        raise RuntimeError("Research export step failed")
    return out


def _fsync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def install(config=CONFIG, target=TARGET, timeout=EXPORT_TIMEOUT_SECONDS, runner=bounded, owner="ubuntu",
            file_owner="scraper", now=time.time, environ=os.environ, scratch=Path("/var/tmp"),
            chown=None, trusted_uid=0):
    """Root side: export unprivileged, validate, atomically replace research.db. Never raises."""
    started = time.monotonic()
    work = staged = None
    try:
        if not Path(config).is_file():
            return {"status": "disabled", "reason": "research database not configured"}
        deadline = environ.get("ILLOURA_MORNING_DEADLINE")
        if deadline:
            try:
                left = float(deadline) - now()
            except ValueError:
                left = 0
            if left < timeout + MORNING_RESERVE_SECONDS:
                return {"status": "skipped", "reason": "morning budget reserved for the brief; previous file kept"}
        st = Path(config).stat()
        if st.st_uid != trusted_uid or st.st_mode & 0o022:
            raise ValueError("Research configuration must be root-owned and not writable by others")
        root = Path(json.loads(Path(config).read_text())["data_root"]).resolve(strict=True)
        import pwd
        account = pwd.getpwnam(owner)
        holder = pwd.getpwnam(file_owner)
        chown = chown or os.chown
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        work = Path(scratch) / ("illoura-research-export-" + stamp)
        work.mkdir(mode=0o700)
        chown(work, account.pw_uid, account.pw_gid)
        script = work / "export.py"
        shutil.copyfile(__file__, script)
        chown(script, account.pw_uid, account.pw_gid)
        output = work / "research.db"
        runner(["runuser", "-u", owner, "--", "nice", "-n", "10", root / "venv/bin/python", script,
                "export", "--environment", root / "database.env", "--destination", output], timeout)
        counts = validate(output)
        target = Path(target)
        staged = target.parent / (".research-" + uuid.uuid4().hex + ".db")
        shutil.copyfile(output, staged)
        with staged.open("r+b") as stream:
            os.fsync(stream.fileno())
        chown(staged, holder.pw_uid, holder.pw_gid)
        staged.chmod(0o640)
        os.replace(staged, target)
        staged = None
        _fsync_directory(target.parent)
        return {"status": "updated", "tables": counts, "seconds": round(time.monotonic() - started, 1)}
    except Exception as error:  # noqa: BLE001 - recorded; publication keeps the previous file
        return {"status": "failed", "error_code": type(error).__name__,
                "seconds": round(time.monotonic() - started, 1)}
    finally:
        if staged is not None:
            Path(staged).unlink(missing_ok=True)
        if work is not None:
            shutil.rmtree(work, ignore_errors=True)


def main(argv=None):
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    one = sub.add_parser("export", help="Research owner: write a new SQLite export")
    one.add_argument("--environment", required=True)
    one.add_argument("--destination", required=True)
    two = sub.add_parser("install", help="Root: export, validate and replace research.db")
    two.add_argument("--timeout", type=int, default=EXPORT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if args.action == "export":
        try:
            result = export(args.environment, args.destination)
        except Exception as error:  # noqa: BLE001 - connection errors can carry credentials
            print(json.dumps({"status": "failed", "error_code": type(error).__name__}))
            return 1
    else:
        result = install(timeout=args.timeout, environ={})
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in ("exported", "updated", "disabled") else 1


if __name__ == "__main__":
    raise SystemExit(main())
