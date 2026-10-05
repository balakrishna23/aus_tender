#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 22.04/24.04 VPS. Upload the whole aus_tender folder
# (including databases/, .env and the Google key .json) and run from inside it:
#   sudo bash deploy/setup_vps.sh
# Afterwards the scrapers run every 6 hours with no manual steps. Re-running is safe
# (use it to deploy code updates; databases on the server are never overwritten).
set -euo pipefail

APP_DIR=/opt/aus_tender
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.browsers"

timedatectl set-timezone Australia/Sydney || true
apt-get update
apt-get install -y python3 python3-venv python3-pip sqlite3 rsync

id scraper >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin scraper

mkdir -p "$APP_DIR/logs" "$APP_DIR/backups" "$APP_DIR/databases"
# Code and config only; server-side data (databases, logs, exports, backups) is never overwritten.
rsync -a --exclude '.venv' --exclude '.browsers' --exclude '__pycache__' --exclude '.git' \
    --exclude 'logs/' --exclude 'backups/' --exclude 'exports/' --exclude 'databases/' \
    --exclude 'databases_live/' --exclude '*.tar.gz' "$SRC_DIR"/ "$APP_DIR"/
# First install only: seed the server with this machine's databases, so the server knows
# what was already scraped (otherwise its first run re-downloads every stored notice).
if [ -z "$(ls -A "$APP_DIR/databases" 2>/dev/null)" ] && [ -d "$SRC_DIR/databases" ]; then
    echo "Seeding databases from $SRC_DIR/databases"
    cp -a "$SRC_DIR/databases/." "$APP_DIR/databases/"
fi

python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip wheel
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements-tested.txt"
# Chromium plus its system libraries, for the ACT and NSW spiders.
"$APP_DIR/.venv/bin/python" -m playwright install --with-deps chromium

if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "Created $APP_DIR/.env from the example - fill it in, then rerun this script."
fi
chown -R scraper:scraper "$APP_DIR"
chmod 600 "$APP_DIR/.env" "$APP_DIR"/*.json 2>/dev/null || true

cp "$APP_DIR/deploy/austender-scraper.service" "$APP_DIR/deploy/austender-scraper.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now austender-scraper.timer

echo
echo "Installed. Next scheduled run:"
systemctl list-timers austender-scraper.timer --no-pager
echo
echo "Now run the first check (about 15 minutes):  sudo bash $APP_DIR/deploy/check_vps.sh --run"
