#!/usr/bin/env python3
"""
Simple batch runner for Scrapy spiders.
- Runs spiders sequentially
- Prints which spider is running
- Shows DB before/after counts and delta
- Shows WARNING/ERROR counts and Google Sheets sync status parsed from the spider log

Usage:
  python run_spiders.py                       # run all spiders in default order
  python run_spiders.py --spiders standing_offer aus_tender
  python run_spiders.py --timeout 3600 --retries 0 --fail-fast
"""

import argparse
import json
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import defaultdict
from pathlib import Path
from typing import Tuple
from dotenv import load_dotenv

from database_manager import DatabaseManager
from project_paths import PROJECT_DIR

DEFAULT_SPIDERS = [
    "contract_api",    # first: aus_contract opens the pages of the notices it lists,
    "aus_contract",    # and standing_offer follows the panel links aus_contract stores
    "aus_tender",
    "planned_procurement",
    "standing_offer",
    "buy_ict",
    "act_tender",
    "nsw_tender",
]

# State-only spiders (run separately with --spiders act_tender nsw_tender)
STATE_SPIDERS = [
    "act_tender",
    "nsw_tender",
]

# Spiders that are standalone scripts (not Scrapy crawl commands).
# Maps spider name → (script path, extra args list)
STANDALONE_SCRIPTS = {
    "nsw_tender": ("nsw_tender_fetch.py", ["--pages", "5"]),
    # AusTender's official contract API: new and amended contract notices since the last run.
    "contract_api": ("austender_api.py", []),
}

LOG_DIR = PROJECT_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)
HISTORY_DAYS = 14
REPORT_DAYS = 90


def acquire_run_lock():
    """Exclusive lock so a scheduled run never overlaps a slow previous one.

    The OS releases it if the process dies, so a crash never leaves a stale lock.
    """
    handle = open(LOG_DIR / ".run.lock", "a+")
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def ping_healthcheck(suffix: str = "", body: str = "") -> None:
    """Optional dead-man's switch (e.g. healthchecks.io): alerts if runs fail or stop arriving."""
    url = os.environ.get("HEALTHCHECK_URL", "").strip()
    if not url:
        return
    import urllib.request
    try:
        urllib.request.urlopen(url.rstrip("/") + suffix, data=body.encode()[:10000], timeout=15)
    except Exception as e:
        print(f"WARNING: health check ping failed: {e}")


def data_quality_warnings(results, reports_dir=None, history=7, exclude=None):
    """Spec section 11: row counts per source after every run, with an alert on a drop of more
    than 30%; also flags a source that brought nothing new when it usually does."""
    reports_dir = reports_dir or LOG_DIR / "runs"
    previous = defaultdict(list)
    earlier = [p for p in sorted(reports_dir.glob("*.json")) if exclude is None or p != Path(exclude)]
    for path in earlier[-history:]:
        try:
            for r in json.loads(path.read_text(encoding="utf-8")).get("results", []):
                previous[r["spider"]].append(r.get("saved") or 0)
        except (ValueError, KeyError, OSError):
            continue
    warnings = []
    for r in results:
        before, after = r.get("before") or 0, r.get("after") or 0
        if before and after < before * 0.7:
            warnings.append(f"{r['spider']}: row count fell {before} -> {after} (more than 30%)")
        usual = sorted(previous.get(r["spider"], []))
        if len(usual) >= 3 and usual[len(usual) // 2] >= 10 and not r.get("saved"):
            warnings.append(f"{r['spider']}: nothing new this run (usually about {usual[len(usual) // 2]})")
    return warnings


def archive_logs(run_id: str, spiders) -> Path:
    """Keep each run's spider logs for HISTORY_DAYS (spider logs are overwritten every run)."""
    target = LOG_DIR / "history" / run_id
    target.mkdir(parents=True, exist_ok=True)
    for spider in spiders:
        if get_spider_log_path(spider).exists():
            shutil.copy2(get_spider_log_path(spider), target / f"{spider}.log")
    cutoff = time.time() - HISTORY_DAYS * 86400
    for old in (LOG_DIR / "history").iterdir():
        if old.is_dir() and old.stat().st_mtime < cutoff:
            shutil.rmtree(old, ignore_errors=True)
    # Small files, but one per run forever adds up on a server; the data stays in the databases.
    report_cutoff = time.time() - REPORT_DAYS * 86400
    for folder in (LOG_DIR / "runs", PROJECT_DIR / "exports" / "releases"):
        for old in folder.glob("*") if folder.is_dir() else ():
            if old.is_file() and old.stat().st_mtime < report_cutoff:
                old.unlink(missing_ok=True)
    return target


def ensure_scrapy_cmd() -> str:
    """Return the scrapy command to use (python -m scrapy is more reliable)."""
    return sys.executable + " -m scrapy"


def get_spider_log_path(spider: str) -> Path:
    return LOG_DIR / f"{spider}.log"


def rotate_log(spider: str) -> None:
    path = get_spider_log_path(spider)
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def collect_db_count(dbm: DatabaseManager, spider: str) -> int:
    """Return row count for spider table; 0 if table doesn't exist yet."""
    try:
        schema = dbm.SCHEMAS.get(spider)
        if not schema:
            return 0
        table = schema["table_name"]
        with dbm.get_connection(spider) as conn:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
            if cur.fetchone() is None:
                return 0
            cur = conn.execute(f"SELECT COUNT(*) FROM {table}")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0


def parse_log_for_metrics(spider: str, log_text: str, gsheets_auto_sync: bool) -> Tuple[int, int, str]:
    """Return (warnings_count, errors_count, sync_status)."""
    warnings_count = len(re.findall(r"\bWARNING:", log_text))
    errors_count = len(re.findall(r"\b(?:ERROR|CRITICAL):", log_text))

    sync_status = "not_enabled" if not gsheets_auto_sync else "unknown"
    if gsheets_auto_sync:
        if re.search(r"Synced \d+ rows to worksheet|Synced standing_offer into worksheets:", log_text):
            sync_status = "success"
        elif re.search(r"Failed to sync .* to Google Sheets:|Google Sheets auto-sync failed .*:|APIError:\s*\[400\]", log_text):
            sync_status = "failed"
    return warnings_count, errors_count, sync_status


def stop_process(proc):
    """Terminate the child and browser descendants on Windows."""
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if proc.poll() is None:
        proc.kill()
    proc.wait()


def stream_command(cmd, timeout: int) -> int:
    """Stream child output while enforcing a timeout even if the child is silent."""
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        cwd=PROJECT_DIR,
        start_new_session=os.name != 'nt',
    )
    lines = queue.Queue()

    def read_output() -> None:
        try:
            for line in proc.stdout:
                lines.put(line)
        finally:
            proc.stdout.close()
            lines.put(None)

    threading.Thread(target=read_output, daemon=True).start()
    deadline = time.monotonic() + timeout if timeout else None
    while True:
        remaining = deadline - time.monotonic() if deadline else None
        if remaining is not None and remaining <= 0:
            stop_process(proc)
            raise subprocess.TimeoutExpired(cmd, timeout)
        try:
            line = lines.get(timeout=min(0.5, remaining) if remaining is not None else 0.5)
        except queue.Empty:
            continue
        if line is None:
            try:
                return proc.wait(timeout=max(0.001, remaining) if remaining is not None else None)
            except subprocess.TimeoutExpired:
                stop_process(proc)
                raise
        sys.stdout.write(line)
        sys.stdout.flush()


def run_standalone(spider: str, timeout: int, retries: int) -> int:
    """Run a standalone Python script (not a Scrapy spider). Returns exit code."""
    script, extra_args = STANDALONE_SCRIPTS[spider]
    script_path = Path(__file__).parent / script
    cmd = [sys.executable, str(script_path)] + extra_args
    attempt = 0
    last_code = 0
    total_attempts = retries + 1
    while attempt < total_attempts:
        attempt += 1
        print(f"Attempt {attempt}/{total_attempts}: starting standalone '{spider}' ({script})...")
        try:
            last_code = stream_command(cmd, timeout)
        except subprocess.TimeoutExpired:
            print(f"ERROR: '{spider}' standalone timed out after {timeout} seconds.")
            last_code = 124
        except Exception as e:
            print(f"ERROR: Failed to run standalone '{spider}': {e}")
            last_code = 1

        if last_code == 0:
            print(f"'{spider}' standalone finished successfully.")
            return 0
        else:
            print(f"'{spider}' standalone exited with code {last_code}.")
            if attempt < total_attempts:
                print("Retrying in 3 seconds...")
                time.sleep(3)
    return last_code


def run_spider(spider: str, timeout: int, retries: int, spider_args=()) -> int:
    """Run a spider via subprocess with live output. Returns exit code (0 on success)."""
    cmd = [sys.executable, "-m", "scrapy", "crawl", spider]
    for arg in spider_args:
        cmd += ["-a", arg]
    attempt = 0
    last_code = 0
    total_attempts = retries + 1
    while attempt < total_attempts:
        attempt += 1
        print(f"Attempt {attempt}/{total_attempts}: starting '{spider}'...")
        try:
            last_code = stream_command(cmd, timeout)
        except subprocess.TimeoutExpired:
            print(f"ERROR: '{spider}' timed out after {timeout} seconds.")
            last_code = 124
        except Exception as e:
            print(f"ERROR: Failed to run '{spider}': {e}")
            last_code = 1

        if last_code == 0:
            print(f"'{spider}' finished successfully.")
            return 0
        else:
            print(f"'{spider}' exited with code {last_code}.")
            if attempt < total_attempts:
                print("Retrying in 3 seconds...")
                time.sleep(3)
    return last_code


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # logs may be redirected to a file
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    load_dotenv(PROJECT_DIR / '.env')
    parser = argparse.ArgumentParser(description="Simple batch runner for Scrapy spiders")
    parser.add_argument("--spiders", nargs="+", choices=DEFAULT_SPIDERS, default=DEFAULT_SPIDERS, help="Spiders to run sequentially")
    parser.add_argument("--timeout", type=int, default=0, help="Timeout per spider (seconds, 0 means no timeout)")
    parser.add_argument("--retries", type=int, default=0, help="Retries per spider on non-zero exit code")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure or sync error")
    parser.add_argument("--no-sync", action="store_true", help="Disable Google Sheets writes for this run")
    parser.add_argument("--report", type=Path, help="Write a JSON batch summary (default: logs/runs/<time>.json)")
    parser.add_argument("--no-feed", action="store_true", help="Do not update the releases feed after the run")
    parser.add_argument("--backfill-since", metavar="YYYY-MM-DD",
                        help="Also fetch older notices: contracts/tenders published since this date, all standing offers")
    args = parser.parse_args()
    if args.timeout < 0 or args.retries < 0:
        parser.error('--timeout and --retries must be non-negative')
    if args.no_sync:
        os.environ['GSHEETS_AUTO_SYNC'] = 'False'

    # Detect Google Sheets auto-sync from env
    gsheets_auto_sync = os.environ.get("GSHEETS_AUTO_SYNC", "false").lower() in ("true","1","t","yes","y")

    lock = acquire_run_lock()
    if lock is None:
        print("Another run is still in progress; skipping this one.")
        return 0
    try:
        return run_batch(args, gsheets_auto_sync)
    finally:
        lock.close()


def run_batch(args, gsheets_auto_sync: bool) -> int:
    run_id = time.strftime("%Y%m%d-%H%M%S")
    if args.report is None:
        args.report = LOG_DIR / "runs" / f"{run_id}.json"
    spider_args = {}
    if args.backfill_since:
        spider_args = {"aus_contract": [f"since={args.backfill_since}"],
                       "aus_tender": [f"since={args.backfill_since}"],
                       "standing_offer": ["since=all"]}
    ping_healthcheck("/start")
    # Spiders run as child processes; they inherit this id so their records join this run.
    os.environ["ACTIVITY_RUN_ID"] = run_id
    import activity_log
    activity_log.start_run(run_id, args.spiders)

    dbm = DatabaseManager()
    any_failed = False
    results = []

    for spider in args.spiders:
        if spider not in DEFAULT_SPIDERS:
            print(f"Skipping unknown spider: {spider}")
            continue

        rotate_log(spider)
        before = collect_db_count(dbm, spider)
        print(f"\n=== Running spider: {spider} ===")
        print(f"Before count: {before}")
        start = time.time()
        if spider in STANDALONE_SCRIPTS:
            code = run_standalone(spider, timeout=args.timeout, retries=args.retries)
        else:
            code = run_spider(spider, timeout=args.timeout, retries=args.retries,
                              spider_args=spider_args.get(spider, ()))
        standalone_sync_status = None
        if gsheets_auto_sync and spider in STANDALONE_SCRIPTS and code == 0:
            try:
                import settings as project_settings
                from gsheets_sync import GoogleSheetsSync

                sync = GoogleSheetsSync(
                    project_settings.GSHEETS_SPREADSHEET_ID,
                    project_settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT,
                )
                standalone_sync_status = "success" if sync.sync_spider(spider) else "failed"
            except Exception as e:
                print(f"ERROR: Google Sheets sync failed for {spider}: {e}")
                standalone_sync_status = "failed"
        duration = round(time.time() - start, 2)
        after = collect_db_count(dbm, spider)
        delta = after if before == 0 and after > 0 else (after - before)

        log_path = get_spider_log_path(spider)
        log_text = read_text(log_path)
        warnings_count, errors_count, sync_status = parse_log_for_metrics(spider, log_text, gsheets_auto_sync)
        if standalone_sync_status is not None:
            sync_status = standalone_sync_status

        response_match = re.search(r"'downloader/response_count':\s*(\d+)", log_text)
        no_responses = spider not in STANDALONE_SCRIPTS and (
            response_match is None or int(response_match.group(1)) == 0
        )
        item_match = re.search(r"'item_scraped_count':\s*(\d+)", log_text)
        saved_match = re.search(r"'database/items_saved':\s*(\d+)", log_text)
        if spider in STANDALONE_SCRIPTS:
            saved_match = re.search(r"(\d+) total notices saved to", log_text)
        duplicate_match = re.search(r"Database duplicates \(persistent\):\s*(\d+)", log_text)
        no_items = spider not in STANDALONE_SCRIPTS and (
            item_match is None or int(item_match.group(1)) == 0
        ) and (
            duplicate_match is None or int(duplicate_match.group(1)) == 0
        )
        unchanged_match = re.search(r"'crawl/items_unchanged':\s*(\d+)", log_text)
        if unchanged_match and int(unchanged_match.group(1)) > 0:
            no_items = False
        database_failed = bool(re.search(r"'database/items_failed':\s*[1-9]", log_text))
        # Ignored HTTP errors and exhausted retries may only appear in stats,
        # with Scrapy still exiting zero and reporting finish_reason=finished.
        crawl_failed = bool(re.search(
            r"'(?:httperror/response_ignored_count|retry/max_reached|"
            r"spider_exceptions/[^']+|crawl/[^']*_failed)':\s*[1-9]", log_text))
        finish_match = re.search(r"'finish_reason':\s*'([^']+)'", log_text)
        incomplete = spider not in STANDALONE_SCRIPTS and (
            finish_match is None or finish_match.group(1) != 'finished'
        )
        missing_sync = (
            gsheets_auto_sync
            and after > 0
            and sync_status == "unknown"
        )
        failed = code != 0 or no_responses or no_items or database_failed or crawl_failed or incomplete or errors_count > 0 or sync_status == "failed" or missing_sync
        any_failed = any_failed or failed

        status = f"FAIL({code})" if code != 0 else (
            "FAIL(no responses)" if no_responses else (
                "FAIL(no items)" if no_items else ("PARTIAL" if failed else "OK")
            )
        )
        print(f"[ {spider} ] {status} | Δ {delta} (before={before}, after={after}) | warnings={warnings_count} errors={errors_count} | sync={sync_status} | {duration}s")
        if errors_count:
            print("-- Last 10 error lines --")
            for ln in [l for l in log_text.splitlines() if "ERROR" in l][-10:]:
                print(ln)
        # If Sheets sync failed, surface the first matching line
        if sync_status == "failed":
            print("-- Google Sheets sync failure detected --")
            for ln in log_text.splitlines():
                if ("Failed to sync" in ln) or ("auto-sync failed" in ln) or ("APIError: [400]" in ln):
                    print(ln)
                    break
        print(f"Log file: {log_path}")
        counts = {}
        for kind in ("new", "updated", "unchanged"):
            match = re.search(rf"'database/items_{kind}':\s*(\d+)", log_text)
            counts[kind] = int(match.group(1)) if match else 0
        skipped_final = int(unchanged_match.group(1)) if unchanged_match else 0
        print(f"[ {spider} ] new={counts['new']} updated={counts['updated']} "
              f"rechecked_unchanged={counts['unchanged']} skipped_already_final={skipped_final}")
        results.append(dict(spider=spider, status=status, exit_code=code, **counts,
                            skipped_final=skipped_final,
                            before=before, after=after, saved=int(saved_match.group(1)) if saved_match else 0,
                            warnings=warnings_count, errors=errors_count, sync=sync_status,
                            crawl_failed=crawl_failed,
                            duration_seconds=duration, log=str(log_path)))
        activity_log.record_scrape(run_id, results[-1])
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({'success': not any_failed, 'results': results}, indent=2), encoding='utf-8')

        if args.fail_fast and failed:
            print("Fail-fast: stopping batch.")
            break

    quality = data_quality_warnings(results, exclude=args.report)
    for line in quality:
        print(f"DATA QUALITY: {line}")

    # The product feed is rebuilt even after a partial failure: what was scraped is still valid.
    feed_line = "releases feed: skipped"
    if not args.no_feed:
        try:
            import build_releases
            feed = build_releases.build()
            feed_line = (f"releases feed: {len(feed['new'])} new, {len(feed['changed'])} changed, "
                         f"{feed['total']} since {feed['feed_start']}")
            if gsheets_auto_sync and not os.environ.get("GSHEETS_FOLDER_ID"):
                import settings as project_settings
                from gsheets_sync import GoogleSheetsSync
                synced = GoogleSheetsSync(project_settings.GSHEETS_SPREADSHEET_ID,
                                          project_settings.GSHEETS_SERVICE_ACCOUNT_JSON_CONTENT).sync_releases()
                feed_line += "; sheet new_releases " + ("updated" if synced else "FAILED")
                any_failed = any_failed or not synced
        except Exception as e:
            any_failed = True
            feed_line = f"releases feed: FAILED ({e})"
        # The engine views (renewals, opportunities, MMR, rates) are derived; rebuilt every run.
        try:
            import build_intelligence
            build_intelligence.main([])
            feed_line += "; intelligence views rebuilt"
        except Exception as e:
            any_failed = True
            feed_line += f"; intelligence views FAILED ({e})"
        # One spreadsheet per dataset in the Shared Drive (publish_sheets.py), refreshed every run.
        if os.environ.get("GSHEETS_FOLDER_ID") and not args.no_sync:
            try:
                import publish_sheets
                feed_line += f"; {len(publish_sheets.publish_all())} spreadsheets refreshed"
            except Exception as e:
                any_failed = True
                feed_line += f"; spreadsheets FAILED ({e})"
        if quality:
            feed_line += "; DATA QUALITY: " + " | ".join(quality)
        print(f"\n{feed_line}")
        if args.report:
            args.report.write_text(json.dumps({'success': not any_failed, 'results': results,
                                               'feed': feed_line}, indent=2), encoding='utf-8')

    archive_logs(run_id, [r["spider"] for r in results])
    try:  # the dashboard is a convenience; never let it fail a run
        import dashboard
        dashboard.build()
    except Exception as e:
        print(f"Dashboard not rebuilt: {e}")
    summary = "\n".join(f"{r['spider']}: {r['status']} new={r['new']} updated={r['updated']} "
                        f"({r['duration_seconds']}s)" for r in results) + "\n" + feed_line
    ping_healthcheck("/fail" if any_failed else "", summary)
    activity_log.finish_run(run_id, "failed" if any_failed else "ok", summary)
    print("\nBatch complete.")
    return 1 if any_failed else 0


if __name__ == "__main__":
    sys.exit(main())
