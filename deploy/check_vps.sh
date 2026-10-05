#!/usr/bin/env bash
# Health report for the VPS install.
#   sudo bash deploy/check_vps.sh          # status of the schedule and the last run
#   sudo bash deploy/check_vps.sh --run    # run everything now (~15 min), then report
set -uo pipefail
APP_DIR=/opt/aus_tender
PY="$APP_DIR/.venv/bin/python"

if [ "${1:-}" = "--run" ]; then
    echo "Running all scrapers now (follow along: journalctl -u austender-scraper.service -f)..."
    systemctl start austender-scraper.service
fi

echo "== Schedule"
systemctl list-timers austender-scraper.timer --no-pager | head -3
echo
echo "== Last run"
systemctl show austender-scraper.service -p ExecMainStatus -p ExecMainExitTimestamp -p MemoryPeak --no-pager
LATEST=$(ls -1t "$APP_DIR"/logs/runs/*.json 2>/dev/null | head -1)
if [ -z "$LATEST" ]; then
    echo "No run report yet."
else
    "$PY" - "$LATEST" <<'EOF'
import json, sys
report = json.load(open(sys.argv[1]))
print(f"Report: {sys.argv[1]}")
for r in report['results']:
    flag = 'OK  ' if r['status'] == 'OK' else 'FAIL'
    print(f"  {flag} {r['spider']:<20} new={r.get('new', 0):<5} updated={r.get('updated', 0):<5} "
          f"errors={r['errors']:<3} {r['duration_seconds']:>7}s")
print(' ', report.get('feed', 'releases feed: n/a'))
print('Overall:', 'ALL OK' if report['success'] else 'SOMETHING FAILED - see logs/<spider>.log')
EOF
fi
echo
echo "== Releases feed (product data)"
sqlite3 "$APP_DIR/databases/releases.db" \
  "SELECT source, COUNT(*) AS releases, SUM(status='open') AS open, MAX(first_seen_at) AS latest FROM releases GROUP BY source;" \
  -header -column 2>/dev/null || echo "No feed yet."
echo
echo "== Resources"
free -h | head -2
df -h "$APP_DIR" | tail -1
du -sh "$APP_DIR/databases" "$APP_DIR/backups" 2>/dev/null
