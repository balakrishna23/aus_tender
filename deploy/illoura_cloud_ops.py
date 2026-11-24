"""Linux cloud publication/recovery operations. No mail sends or AWS credentials.

Source and workflow writers must use the same run locks. Only validated complete
runs may be published. Served generations are immutable and never modified.
"""

import argparse
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import uuid


def _retention_time(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("Retention timestamps must include a timezone")
    return moment.astimezone(timezone.utc)


def _managed_directory(path, root):
    """Reject links, junctions and paths outside the explicitly managed root."""
    try:
        return (
            path.is_dir()
            and not path.is_symlink()
            and path.parent == root
            and path.resolve() == path
        )
    except (OSError, RuntimeError):
        return False


def _protected_generations(served):
    protected = set()
    for name in ("current", "previous"):
        pointer = served / name
        if not os.path.lexists(pointer):
            if name == "current":
                raise ValueError("Current generation pointer is missing")
            continue
        if not pointer.is_symlink():
            raise ValueError("Generation pointers must be symlinks")
        try:
            target = pointer.resolve(strict=True)
        except (OSError, RuntimeError) as error:
            raise ValueError("Generation pointer is broken") from error
        if not _managed_directory(target, served):
            raise ValueError("Generation pointer escapes the managed root")
        protected.add(target.name)
    return protected


def _directory_bytes(path):
    """Logical regular-file bytes; this is not a promise of physical blocks freed."""
    total = 0
    boundary = path.resolve()
    for directory, children, files in os.walk(path, followlinks=False):
        parent = Path(directory)
        children[:] = [
            name
            for name in children
            if not (parent / name).is_symlink()
            and (parent / name).resolve() == parent / name
            and (parent / name).resolve().is_relative_to(boundary)
        ]
        for name in files:
            file = parent / name
            if not file.is_symlink() and file.is_file():
                total += file.stat().st_size
    return total


def _completed_time(path, backup=False):
    filename = "recovery_manifest.json" if backup else "manifest.json"
    manifest_path = path / filename
    if manifest_path.is_symlink():
        raise ValueError("Manifest must not be a symlink")
    meta = json.loads(manifest_path.read_text(encoding="utf-8"))
    if backup:
        files = meta["files"]
        if not isinstance(files, dict):
            raise ValueError("Invalid backup file manifest")
        for name, entry in files.items():
            relative = Path(name)
            file = path / relative
            if (
                relative.is_absolute()
                or ".." in relative.parts
                or file.is_symlink()
                or not file.is_file()
                or not file.resolve().is_relative_to(path.resolve())
            ):
                raise ValueError("Incomplete or unsafe backup")
            if not isinstance(entry, dict) or entry.get("sha256") != sha(file):
                raise ValueError(
                    "Backup checksum does not match its completion manifest"
                )
        return _retention_time(meta["created_at_utc"])
    if not isinstance(meta["datasets"], dict):
        raise ValueError("Invalid generation manifest")
    for dataset, entry in meta["datasets"].items():
        if not dataset.replace("_", "").isalnum() or entry["file"] != dataset + ".db":
            raise ValueError("Unsafe generation manifest")
        file = path / entry["file"]
        if file.is_symlink() or not file.is_file():
            raise ValueError("Incomplete generation")
    return _retention_time(meta["snapshot_finished_utc"])


def _remove_retention_directory(path, root):
    # Recheck the absolute target immediately before any recursive deletion.
    if not _managed_directory(path, root):
        raise ValueError("Deletion target is outside the managed root or is a link")
    boundary = path.resolve()
    # Published generations are read-only. Only the selected obsolete tree is
    # made owner-writable; protected generations never enter this function.
    for directory, children, files in os.walk(path, followlinks=False):
        parent = Path(directory)
        if not parent.resolve().is_relative_to(boundary):
            raise ValueError("Unexpected directory escape during deletion")
        parent.chmod(0o700)
        children[:] = [
            name
            for name in children
            if not (parent / name).is_symlink()
            and (parent / name).resolve() == parent / name
            and (parent / name).resolve().is_relative_to(boundary)
        ]
        if os.name == "nt":
            for name in files:
                file = parent / name
                if not file.is_symlink() and file.resolve().is_relative_to(boundary):
                    file.chmod(0o600)
    shutil.rmtree(path)


def prune(served, backups=None, *, now=None, dry_run=False):
    """Prune managed storage while caller holds operations/routine/source locks.

    API reads are unchanged: current, previous and three newest older generations
    are retained. Unknown/incomplete backup or generation directories are reported
    for attention, never treated as completed copies or silently deleted.
    """
    served = Path(served).resolve(strict=True)
    backups = Path(backups).resolve() if backups is not None else None
    if backups is not None and (
        backups.is_relative_to(served) or served.is_relative_to(backups)
    ):
        raise ValueError("Generation and backup retention roots must be disjoint")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("Retention clock must include a timezone")
    protected = _protected_generations(served)
    report = {
        "status": "ok",
        "checked_at_utc": now.isoformat(),
        "dry_run": dry_run,
        "policy": {
            "older_generations": 3,
            "operations_backups": 14,
            "failed_staging_age_seconds": 2 * 86400,
        },
        "protected_generations": sorted(protected),
        "kept_generations": [],
        "kept_backups": [],
        "planned_deletions": [],
        "deleted": [],
        "skipped": [],
        "errors": [],
        "reclaimed_bytes": 0,
    }
    completed = []
    staging = []
    for path in served.iterdir():
        generation = path.name.startswith(("generation-", "snapshot-"))
        failed = path.name.startswith(".generation-")
        if not (generation or failed):
            continue
        if not _managed_directory(path, served):
            report["skipped"].append(
                {"name": path.name, "reason": "not a managed directory"}
            )
            continue
        if failed:
            if (
                path.name not in protected
                and now.timestamp() - path.stat().st_mtime > 2 * 86400
            ):
                staging.append(path)
            continue
        try:
            completed.append((_completed_time(path), path.name, path))
        except (OSError, ValueError, KeyError, TypeError):
            report["errors"].append(
                {
                    "name": path.name,
                    "reason": "incomplete or invalid generation manifest",
                }
            )
    # Protected targets are retained even if their manifest needs manual repair.
    older = sorted((row for row in completed if row[1] not in protected), reverse=True)
    kept = protected | {row[1] for row in older[:3]}
    report["kept_generations"] = sorted(kept)
    candidates = [(row[2], served, "older generation") for row in older[3:]]
    candidates += [
        (path, served, "failed staging older than two days") for path in staging
    ]
    if backups is not None and backups.exists():
        complete_backups = []
        for path in backups.glob("operations-*"):
            if not _managed_directory(path, backups):
                report["skipped"].append(
                    {"name": path.name, "reason": "not a managed backup directory"}
                )
                continue
            try:
                complete_backups.append(
                    (_completed_time(path, backup=True), path.name, path)
                )
            except (OSError, ValueError, KeyError, TypeError):
                report["errors"].append(
                    {
                        "name": path.name,
                        "reason": "incomplete or invalid backup retained",
                    }
                )
        complete_backups.sort(reverse=True)
        report["kept_backups"] = [row[1] for row in complete_backups[:14]]
        candidates += [
            (row[2], backups, "backup outside newest fourteen")
            for row in complete_backups[14:]
        ]
    volumes = {}
    for root in [served] + (
        [backups] if backups is not None and backups.exists() else []
    ):
        volumes.setdefault(root.stat().st_dev, (root, shutil.disk_usage(root).free))
    for path, root, reason in candidates:
        entry = {
            "name": path.name,
            "kind": "backup" if root == backups else "generation",
            "reason": reason,
            "bytes": _directory_bytes(path),
        }
        report["planned_deletions"].append(entry)
        if dry_run:
            continue
        try:
            if root == served and path.name in _protected_generations(served):
                raise ValueError("Deletion candidate became protected")
            _remove_retention_directory(path, root)
            report["deleted"].append(entry)
            report["reclaimed_bytes"] += entry["bytes"]
        except (OSError, ValueError) as error:
            report["errors"].append(
                {"name": path.name, "error_type": type(error).__name__}
            )
    report["estimated_reclaimable_bytes"] = sum(
        item["bytes"] for item in report["planned_deletions"]
    )
    report["observed_free_bytes_change"] = sum(
        shutil.disk_usage(root).free - before for root, before in volumes.values()
    )
    report["retained_generation_bytes"] = sum(
        _directory_bytes(served / name) for name in kept if (served / name).is_dir()
    )
    report["retained_backup_bytes"] = (
        sum(_directory_bytes(backups / name) for name in report["kept_backups"])
        if backups is not None
        else 0
    )
    if report["errors"]:
        report["status"] = "attention"
    if not dry_run:
        atomic_json(served / "retention.json", report)
    return report


def _prune_after_publish(served, backups):
    """A cleanup error cannot undo or disguise successful publication."""
    try:
        return prune(served, backups)
    except Exception as error:
        report = {
            "status": "attention",
            "checked_at_utc": utc(),
            "reclaimed_bytes": 0,
            "errors": [{"error_type": type(error).__name__}],
        }
        try:
            atomic_json(Path(served) / "retention.json", report)
        except OSError:
            report["report_write_failed"] = True
        return report


EXPECTED = {
    "contract_api",
    "aus_contract",
    "aus_tender",
    "planned_procurement",
    "standing_offer",
    "buy_ict",
    "act_tender",
    "nsw_tender",
}
BRIEF_SOURCES = {"buy_ict"}  # The daily brief is built from BuyICT only.


def utc():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temp.open("x", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(temp, path)
    if os.name != "nt":
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def sha(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


@contextmanager
def exclusive(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as f:
        if os.name == "nt":
            import msvcrt

            if not path.stat().st_size:
                f.write(b"0")
                f.flush()
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def gaps_approved_by_run(run, gaps):
    """The run script itself let this run continue with exactly these source gaps."""
    try:
        state = json.loads((Path(run) / "cloud_run.json").read_text())
        return state.get("source_exit_code") in (0, 1) and state.get("source_gaps") == gaps
    except (OSError, ValueError, AttributeError):
        return False


def validate_run(run):
    run = Path(run)
    report = json.loads((run / "source_refresh.json").read_text())
    results = report.get("results", [])
    if (
        len(results) != 8
        or not all(isinstance(r, dict) for r in results)
        or {r.get("spider") for r in results} != EXPECTED
        or not isinstance(report.get("feed"), str)
        or not report["feed"].strip()
        or "FAILED" in report["feed"]
    ):
        raise ValueError("Complete eight-source report required")
    gaps = sorted(
        r["spider"]
        for r in results
        if r.get("status") != "OK" or r.get("crawl_failed") or r.get("errors", 0)
    )
    # A clean report, or one whose only problems are in sources the brief does not use.
    # Their databases keep the last good rows (raw data is never overwritten).
    if not gaps:
        if report.get("success") is not True:
            raise ValueError("Complete successful eight-source report required")
    elif (
        BRIEF_SOURCES & set(gaps)
        or report.get("success") is not False
        or not isinstance(report["feed"], str)
        or "FAILED" in report["feed"]
        or not gaps_approved_by_run(run, gaps)
    ):
        raise ValueError("Complete successful eight-source report required")
    final = json.loads((run / "finalization.json").read_text())
    passed = {s["step"] for s in final.get("steps", []) if s.get("exit_code") == 0}
    if not {"intelligence", "data_quality", "newsletter_evidence"} <= passed:
        raise ValueError("Essential evidence steps incomplete")
    evidence = json.loads((run / "newsletter_evidence.json").read_text())
    builds = evidence.get("build_info")
    if (
        not isinstance(builds, list)
        or len(builds) != 1
        or not builds[0].get("built_at")
    ):
        raise ValueError("Exactly one intelligence build timestamp required")
    return {
        "run_id": run.name,
        "source_gaps": gaps,
        "source_results": [
            {
                k: r.get(k)
                for k in [
                    "spider",
                    "status",
                    "new",
                    "updated",
                    "saved",
                    "warnings",
                    "errors",
                ]
            }
            for r in results
        ],
        "evidence_generated_at_utc": evidence["generated_at_utc"],
        "intelligence_build_info": evidence.get("build_info", []),
    }


def copy_database(source, target):
    source = Path(source)
    if source.is_symlink() or not source.is_file():
        raise ValueError("Regular source DB required: " + source.name)
    with closing(
        sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    ) as src:
        with closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            if dst.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Database integrity check failed: " + source.name)


def verify_generation(root):
    root = Path(root)
    meta = json.loads((root / "manifest.json").read_text())
    datasets = meta.get("datasets") if isinstance(meta, dict) else None
    if not isinstance(datasets, dict) or not datasets:
        # An empty generation would serve nothing; it is never ready to go live.
        raise ValueError("Generation manifest lists no datasets")
    for dataset, item in datasets.items():
        if not dataset.replace("_", "").isalnum() or item["file"] != dataset + ".db":
            raise ValueError("Unsafe dataset in manifest")
        file = root / item["file"]
        if file.is_symlink() or sha(file) != item["sha256"]:
            raise ValueError("Snapshot hash mismatch: " + dataset)
        with closing(
            sqlite3.connect(file.resolve().as_uri() + "?mode=ro", uri=True)
        ) as db:
            if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise ValueError("Invalid snapshot: " + dataset)
            declared = item.get("tables") or {}
            present = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')")}
            missing = sorted(set(declared) - present)
            if missing:
                raise ValueError("Snapshot " + dataset + " lacks declared tables: " + ", ".join(missing))
    return meta


def publish(data, served, run, policy, api_group=None, *, backups=None):
    data, served, run = Path(data), Path(served), Path(run)
    evidence = validate_run(run)
    served.mkdir(parents=True, exist_ok=True)
    current = served / "current"
    if current.exists() and not current.is_symlink():
        raise ValueError("Current must be a generation symlink")
    old = current.resolve() if current.exists() else None
    if old and old.parent != served.resolve():
        raise ValueError("Current generation outside managed root")
    required = (
        sum((data / (name + ".db")).stat().st_size for name in policy) + 2 * 1024**3
    )
    if shutil.disk_usage(served).free < required:
        raise ValueError("Insufficient disk space for a complete generation")
    key = (
        "generation-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    staging = served / ("." + key)
    generation = served / key
    staging.mkdir(mode=0o750)
    meta = {
        "generation_id": key,
        "snapshot_started_utc": utc(),
        "datasets": {},
        "collector": evidence,
        "notes": [
            "AWS collector is the procurement source of truth for this generation.",
            "Independent consistent SQLite backups taken while source run locks are held.",
            "Source dates may predate this snapshot; inspect retrieval fields and current deadlines.",
            "Internal workflow and research datasets are served to authorised Illoura keys only.",
        ],
    }
    # Any failure before the pointer switch below leaves the staging directory as
    # evidence (retention expires it after two days); no active generation changes.
    for dataset, tables in policy.items():
        if not dataset.replace("_", "").isalnum():
            raise ValueError("Unsafe dataset name")
        target = staging / (dataset + ".db")
        copy_database(data / target.name, target)
        with closing(sqlite3.connect(target)) as db:
            available = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if not set(tables) <= available:
                raise ValueError("Required tables missing: " + dataset)
            counts = {
                t: db.execute(
                    'SELECT count(*) FROM "' + t.replace('"', '""') + '"'
                ).fetchone()[0]
                for t in tables
            }
            if dataset == "intelligence" and evidence["intelligence_build_info"]:
                actual = db.execute("SELECT built_at FROM build_info").fetchone()[0]
                if actual != evidence["intelligence_build_info"][0]["built_at"]:
                    raise ValueError("Intelligence no longer matches reviewed run")
        meta["datasets"][dataset] = {
            "file": target.name,
            "bytes": target.stat().st_size,
            "sha256": sha(target),
            "tables": counts,
            "backup_finished_utc": utc(),
        }
    meta["snapshot_finished_utc"] = utc()
    atomic_json(staging / "manifest.json", meta)
    # Files are closed and durable before the generation becomes reachable.
    for file in staging.iterdir():
        with file.open("r+b") as f:
            os.fsync(f.fileno())
        if api_group:
            shutil.chown(file, group=api_group)
        file.chmod(0o440)
    if api_group:
        shutil.chown(staging, group=api_group)
    staging.chmod(0o550)
    if os.name != "nt":
        fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    staging.rename(generation)
    if old:
        prior = served / (".previous-" + uuid.uuid4().hex)
        prior.symlink_to(old.name, target_is_directory=True)
        os.replace(prior, served / "previous")
    link = served / (".current-" + uuid.uuid4().hex)
    link.symlink_to(generation.name, target_is_directory=True)
    os.replace(link, current)
    _fsync_directory(served)
    meta["published"] = True
    meta["retention"] = _prune_after_publish(served, backups)
    meta["retention_attention"] = meta["retention"]["status"] != "ok"
    return meta


def _fsync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _managed_generation(pointer, served):
    target = pointer.resolve(strict=True)
    if target.parent != served.resolve() or not target.name.startswith(
        ("generation-", "snapshot-")
    ):
        raise ValueError("Rollback pointer outside managed generation root")
    return target


def rollback(served):
    """Swap current and previous, so a second rollback returns to the newer generation.

    Both targets stay protected throughout: if the second pointer update fails, both
    pointers name the restored generation and the newer one is still among the three
    newest older generations that retention keeps.
    """
    served = Path(served)
    newer = _managed_generation(served / "current", served)
    target = _managed_generation(served / "previous", served)
    if target == newer:
        raise ValueError(
            "Nothing to roll back: current and previous are the same generation"
        )
    meta = verify_generation(target)
    new_current = served / (".rollback-" + uuid.uuid4().hex)
    new_previous = served / (".previous-" + uuid.uuid4().hex)
    new_current.symlink_to(target.name, target_is_directory=True)
    new_previous.symlink_to(newer.name, target_is_directory=True)
    os.replace(new_current, served / "current")
    os.replace(new_previous, served / "previous")
    _fsync_directory(served)
    return {
        "status": "rolled_back",
        "generation_id": meta.get("generation_id", target.name),
        "previous_generation": newer.name,
    }


def backup_operations(project, work, backups):
    project, work, backups = Path(project), Path(work), Path(backups)
    root = backups / (
        "operations-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    root.mkdir(parents=True, mode=0o700)
    files = {}
    # No OAuth/API keys or .env in this portable application recovery set.
    for name in [
        "actions.db",
        "activity.db",
        "bdm_monitor.db",
        "bdm_workflow.db",
        "research_sources.db",
        "rap_evidence.db",
        "data_quality.db",
        "organisations.db",
    ]:
        source = project / "databases" / name
        if source.exists():
            copy_database(source, root / name)
    for source in (work / "reports").glob("daily_sweep_*"):
        if source.is_symlink() or not source.is_dir():
            continue
        for name in [
            "send_attempt.json",
            "send_receipt.json",
            "send_reconciliation.json",
            "cloud_send_review.json",
            "cloud_run.json",
            "newsletter_review.json",
            "editorial_notes.json",
        ]:
            file = source / name
            if file.is_file() and not file.is_symlink():
                destination = root / "reports" / source.name / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(file, destination)
    for source in [
        work / "latest_run.json",
        project / "config/organisation_aliases.yaml",
        project / "config/opt_outs.yaml",
        project / "config/scoring.yaml",
    ]:
        if source.is_file() and not source.is_symlink():
            shutil.copyfile(source, root / source.name)
    for file in root.rglob("*"):
        if file.is_file():
            file.chmod(0o600)
            files[str(file.relative_to(root))] = {
                "sha256": sha(file),
                "bytes": file.stat().st_size,
            }
    atomic_json(
        root / "recovery_manifest.json",
        {
            "created_at_utc": utc(),
            "files": files,
            "credentials_included": False,
            "requires_mail_sent_reconciliation_before_resume": True,
        },
    )
    return root


def restore_drill(backup):
    backup = Path(backup)
    meta = json.loads((backup / "recovery_manifest.json").read_text())
    receipts = 0
    attempts = 0
    databases = 0
    attempt_runs = set()
    receipt_runs = set()
    reconciled_runs = set()
    with tempfile.TemporaryDirectory(prefix="illoura-restore-") as folder:
        restored = Path(folder)
        for name, expected in meta["files"].items():
            relative = Path(name)
            if relative.is_absolute() or ".." in relative.parts:
                raise ValueError("Unsafe recovery entry")
            source = backup / relative
            if source.is_symlink() or sha(source) != expected["sha256"]:
                raise ValueError("Recovery checksum mismatch")
            dest = restored / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, dest)
            if dest.suffix == ".db":
                with closing(sqlite3.connect(dest)) as db:
                    if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                        raise ValueError("Restored DB corrupt")
                databases += 1
            if dest.name == "send_receipt.json":
                value = json.loads(dest.read_text())
                if value.get("sent") is not True:
                    raise ValueError("Receipt lost sent state")
                receipts += 1
                receipt_runs.add(relative.parent.as_posix())
            if dest.name == "send_attempt.json":
                attempts += 1
                attempt_runs.add(relative.parent.as_posix())
            if dest.name == "send_reconciliation.json":
                value = json.loads(dest.read_text())
                if value.get("delivered") is False:
                    reconciled_runs.add(relative.parent.as_posix())
        # Recovery never starts a collector or transport; mail needs Sent reconciliation.
    unresolved = sorted(attempt_runs - receipt_runs - reconciled_runs)
    # An attempt without a receipt may already have been delivered: never "passed".
    return {
        "status": "attention" if unresolved else "passed",
        "restored_files": len(meta["files"]),
        "databases": databases,
        "send_receipts": receipts,
        "send_attempts": attempts,
        "reconciled_undelivered": len(reconciled_runs),
        "mail_replay_performed": False,
        "unresolved_send_runs": unresolved,
        "requires_mail_sent_reconciliation_before_resume": True,
        "checked_at_utc": utc(),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "action", choices=["publish", "backup", "restore-drill", "rollback", "prune"]
    )
    p.add_argument("--project", type=Path, default=Path("/opt/aus_tender"))
    p.add_argument("--work", type=Path, default=Path("/opt/illoura-routine"))
    p.add_argument("--served", type=Path, default=Path("/srv/illoura-data"))
    p.add_argument("--backups", type=Path, default=Path("/var/backups/illoura"))
    p.add_argument("--policy", type=Path, default=Path("/opt/illoura-ops/policy.json"))
    p.add_argument("--backup", type=Path)
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview pruning without deleting or writing a report",
    )
    a = p.parse_args()
    if a.dry_run and a.action != "prune":
        p.error("--dry-run is supported only for prune")
    if a.action == "restore-drill":
        if not a.backup:
            p.error("--backup required")
        with (
            exclusive(a.work / "operations.lock"),
            exclusive(a.work / "routine.lock"),
            exclusive(a.project / "logs/.run.lock"),
        ):
            result = restore_drill(a.backup)
        print(json.dumps(result))
        if result["status"] != "passed":
            raise SystemExit(1)
        return
    with (
        exclusive(a.work / "operations.lock"),
        exclusive(a.work / "routine.lock"),
        exclusive(a.project / "logs/.run.lock"),
    ):
        if a.action == "backup":
            result = {"backup": str(backup_operations(a.project, a.work, a.backups))}
        elif a.action == "rollback":
            result = rollback(a.served)
        elif a.action == "prune":
            result = prune(a.served, a.backups, dry_run=a.dry_run)
        else:
            state = json.loads((a.work / "latest_run.json").read_text())
            run = Path(state["run_dir"])
            if (
                run.parent.resolve() != (a.work / "reports").resolve()
                or run.is_symlink()
            ):
                raise ValueError("Unexpected source run path")
            result = publish(
                a.project / "databases",
                a.served,
                run,
                json.loads(a.policy.read_text()),
                "illoura-api",
                backups=a.backups,
            )
            atomic_json(
                a.work / "publication.json",
                {
                    "status": "published",
                    "generation_id": result["generation_id"],
                    "snapshot_finished_utc": result["snapshot_finished_utc"],
                    "run_id": run.name,
                    "retention": result["retention"],
                    "retention_attention": result["retention_attention"],
                },
            )
    print(json.dumps(result, indent=2))
    if result.get("retention", result).get("status") == "attention":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
