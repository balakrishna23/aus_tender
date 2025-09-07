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
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from database_manager import DatabaseManager

DEFAULT_SPIDERS = [
    "aus_contract",
    "aus_tender",
    "planned_procurement",
    "standing_offer",
]

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


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


def parse_log_for_metrics(spider: str, log_text: str, gsheets_auto_sync: bool) -> tuple[int, int, str]:
    """Return (warnings_count, errors_count, sync_status)."""
    warnings_count = len(re.findall(r"\bWARNING\b", log_text))
    errors_count = len(re.findall(r"\bERROR\b", log_text))

    sync_status = "not_enabled" if not gsheets_auto_sync else "unknown"
    if gsheets_auto_sync:
        if re.search(r"✅ Synced \\d+ rows to worksheet|✅ Synced standing_offer into worksheets:", log_text):
            sync_status = "success"
        elif re.search(r"❌ Failed to sync .* to Google Sheets:|Google Sheets auto-sync failed .*:|APIError:\s*\[400\]", log_text):
            sync_status = "failed"
    return warnings_count, errors_count, sync_status


def run_spider(spider: str, timeout: int, retries: int) -> int:
    """Run a spider via subprocess with live output. Returns exit code (0 on success)."""
    cmd = [sys.executable, "-m", "scrapy", "crawl", spider]
    attempt = 0
    last_code = 0
    total_attempts = retries + 1
    while attempt < total_attempts:
        attempt += 1
        print(f"Attempt {attempt}/{total_attempts}: starting '{spider}'...")
        start = time.time()
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                universal_newlines=True,
            )
            # Stream output to terminal
            while True:
                line = proc.stdout.readline()
                if not line and proc.poll() is not None:
                    break
                if line:
                    sys.stdout.write(line)
                    sys.stdout.flush()
                # Timeout check
                if timeout and (time.time() - start) > timeout:
                    proc.kill()
                    raise subprocess.TimeoutExpired(cmd, timeout)
            last_code = proc.returncode or 0
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
    parser = argparse.ArgumentParser(description="Simple batch runner for Scrapy spiders")
    parser.add_argument("--spiders", nargs="*", default=DEFAULT_SPIDERS, help="Spiders to run sequentially")
    parser.add_argument("--timeout", type=int, default=0, help="Timeout per spider (seconds, 0 means no timeout)")
    parser.add_argument("--retries", type=int, default=0, help="Retries per spider on non-zero exit code")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failure or sync error")
    args = parser.parse_args()

    # Detect Google Sheets auto-sync from env
    gsheets_auto_sync = os.environ.get("GSHEETS_AUTO_SYNC", "false").lower() in ("true","1","t","yes","y")

    dbm = DatabaseManager()

    for spider in args.spiders:
        if spider not in DEFAULT_SPIDERS:
            print(f"Skipping unknown spider: {spider}")
            continue

        rotate_log(spider)
        before = collect_db_count(dbm, spider)
        print(f"\n=== Running spider: {spider} ===")
        print(f"Before count: {before}")
        start = time.time()
        code = run_spider(spider, timeout=args.timeout, retries=args.retries)
        duration = round(time.time() - start, 2)
        after = collect_db_count(dbm, spider)
        delta = after if before == 0 and after > 0 else (after - before)

        log_path = get_spider_log_path(spider)
        log_text = read_text(log_path)
        warnings_count, errors_count, sync_status = parse_log_for_metrics(spider, log_text, gsheets_auto_sync)

        status = "OK" if code == 0 else f"FAIL({code})"
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

        if args.fail_fast and (code != 0 or sync_status == "failed"):
            print("Fail-fast: stopping batch.")
            return 1

    print("\nBatch complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
