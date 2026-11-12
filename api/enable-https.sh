#!/usr/bin/env bash
set -euo pipefail
HOST="$1"
python3 -m venv /opt/illoura-certbot
/opt/illoura-certbot/bin/pip -q install 'certbot>=5.4,<7'
/opt/illoura-certbot/bin/certbot certonly --non-interactive --agree-tos --register-unsafely-without-email --preferred-profile shortlived --webroot --webroot-path /var/lib/letsencrypt --ip-address "$HOST" --cert-name illoura-api
cat > /etc/nginx/sites-available/illoura-api <<NGINX
server {
 listen 80 default_server;
 server_name $HOST;
 server_tokens off;
 location /.well-known/acme-challenge/ { root /var/lib/letsencrypt; }
 location / { return 308 https://\$host\$request_uri; }
}
server {
 listen 443 ssl default_server;
 server_name $HOST;
 server_tokens off;
 ssl_certificate /etc/letsencrypt/live/illoura-api/fullchain.pem;
 ssl_certificate_key /etc/letsencrypt/live/illoura-api/privkey.pem;
 ssl_protocols TLSv1.2 TLSv1.3;
 client_max_body_size 32k;
 client_body_timeout 15s;
 add_header Strict-Transport-Security "max-age=31536000" always;
 add_header X-Content-Type-Options nosniff always;
 location / {
  proxy_pass http://127.0.0.1:8000;
  proxy_http_version 1.1;
  proxy_set_header Host \$host;
  proxy_set_header X-Forwarded-Proto https;
  proxy_set_header X-Forwarded-For \$remote_addr;
  proxy_buffering off;
  proxy_read_timeout 30s;
  access_log off;
 }
}
NGINX
nginx -t
systemctl reload nginx
install -d /etc/letsencrypt/renewal-hooks/deploy
printf '#!/bin/sh\nsystemctl reload nginx\n' > /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
chmod 0755 /etc/letsencrypt/renewal-hooks/deploy/reload-nginx
cat > /etc/systemd/system/illoura-cert-renew.service <<'UNIT'
[Unit]
Description=Renew Illoura API HTTPS certificate
[Service]
Type=oneshot
ExecStart=/opt/illoura-certbot/bin/certbot renew --quiet
UNIT
cat > /etc/systemd/system/illoura-cert-renew.timer <<'UNIT'
[Unit]
Description=Check Illoura API certificate renewal twice daily
[Timer]
OnCalendar=*-*-* 00,12:00:00
RandomizedDelaySec=1800
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now illoura-cert-renew.timer
systemctl is-active illoura-api nginx
echo 'HTTPS enabled. Only the TLS maintenance timer is enabled; no collection or mail schedules.'
