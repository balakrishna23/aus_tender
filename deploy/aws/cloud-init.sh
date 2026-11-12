#!/bin/sh
set -eu
umask 027
export DEBIAN_FRONTEND=noninteractive
timedatectl set-timezone Australia/Sydney
apt-get update
apt-get install -y python3 python3-venv python3-pip sqlite3 ca-certificates
id scraper >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin scraper
install -d -o scraper -g scraper -m 0750 /opt/aus_tender /opt/aus_tender/databases /opt/aus_tender/exports /opt/aus_tender/logs
printf '%s\n' 'Illoura trial base ready. Install the reviewed code kit manually. No application timers installed.' > /opt/aus_tender/TRIAL_BASE_READY
