#!/usr/bin/env bash
set -euo pipefail
umask 027
export DEBIAN_FRONTEND=noninteractive
HOST="$1"
STAGE=/home/ubuntu/illoura-api-kit
apt-get update -qq
apt-get install -y python3-venv nginx sqlite3 ca-certificates
timedatectl set-timezone Australia/Sydney
id scraper >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin scraper
id illoura-api >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin illoura-api
install -d -o root -g illoura-api -m 0750 /opt/illoura-api /etc/illoura-api /srv/illoura-data /srv/illoura-data/snapshot-20261112
if [ ! -f /srv/illoura-data/snapshot-20261112/manifest.json ]; then
 tar -xzf /home/ubuntu/procurement-data.tar.gz -C /srv/illoura-data/snapshot-20261112 --no-same-owner
fi
python3 - <<'PY'
import hashlib,json,sqlite3
from pathlib import Path
p=Path('/srv/illoura-data/snapshot-20261112');m=json.loads((p/'manifest.json').read_text())
for name,entry in m['datasets'].items():
    f=p/entry['file']
    with f.open('rb') as h: assert hashlib.file_digest(h,'sha256').hexdigest()==entry['sha256'],name
    with sqlite3.connect(f.as_uri()+'?mode=ro',uri=True) as db: assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok',name
print('All uploaded database hashes and SQLite integrity checks passed.')
PY
chown -R root:illoura-api /srv/illoura-data
find /srv/illoura-data -type d -exec chmod 0750 {} +
find /srv/illoura-data -type f -exec chmod 0640 {} +
ln -sfn /srv/illoura-data/snapshot-20261112 /srv/illoura-data/current
install -o root -g illoura-api -m 0640 "$STAGE/app.py" /opt/illoura-api/app.py
install -o root -g illoura-api -m 0640 "$STAGE/requirements.txt" /opt/illoura-api/requirements.txt
install -o root -g illoura-api -m 0640 "$STAGE/keys.json" /etc/illoura-api/keys.json
printf 'ILLOURA_DATA_DIR=/srv/illoura-data/current\nILLOURA_KEYS_FILE=/etc/illoura-api/keys.json\nILLOURA_PUBLIC_HOST=%s\n' "$HOST" > /etc/illoura-api/environment
chmod 0640 /etc/illoura-api/environment
chown root:illoura-api /etc/illoura-api/environment
python3 -m venv /opt/illoura-api/.venv
/opt/illoura-api/.venv/bin/pip -q install -r /opt/illoura-api/requirements.txt
chown -R root:illoura-api /opt/illoura-api
chmod -R g+rX /opt/illoura-api
cat > /etc/systemd/system/illoura-api.service <<'UNIT'
[Unit]
Description=Illoura read-only procurement API and MCP
After=network-online.target
Wants=network-online.target
[Service]
User=illoura-api
Group=illoura-api
WorkingDirectory=/opt/illoura-api
EnvironmentFile=/etc/illoura-api/environment
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/illoura-api/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --workers 1 --no-access-log --timeout-keep-alive 10 --limit-concurrency 12 --limit-max-requests 10000
Restart=always
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
CapabilityBoundingSet=
UMask=0077
MemoryMax=1G
TasksMax=64
CPUQuota=100%
[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now illoura-api.service
install -d -m 0755 /var/lib/letsencrypt/.well-known/acme-challenge
cat > /etc/nginx/sites-available/illoura-api <<NGINX
server {
 listen 80 default_server;
 server_name $HOST;
 server_tokens off;
 location /.well-known/acme-challenge/ { root /var/lib/letsencrypt; }
 location / { return 308 https://\$host\$request_uri; }
}
NGINX
rm -f /etc/nginx/sites-enabled/default
ln -sf /etc/nginx/sites-available/illoura-api /etc/nginx/sites-enabled/illoura-api
nginx -t
systemctl enable --now nginx
systemctl reload nginx
echo 'API installed on loopback. Ready for public certificate issuance.'
