#!/usr/bin/env bash
set -euo pipefail
umask 077
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"

rotate_admin=0
if [[ ${1:-} == --rotate-admin ]]; then rotate_admin=1; shift; fi
if [[ $EUID -ne 0 || $# -lt 2 || $# -gt 3 ]]; then
  echo 'Usage: sudo bash install.sh [--rotate-admin] DOMAIN ACME_EMAIL [WEBSITE_DOMAIN]' >&2
  exit 2
fi
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || { echo 'Ubuntu 24.04 is required.' >&2; exit 2; }
domain=$1
email=$2
root_domain=${3:-}
export EDGE_DOMAIN="$domain" EDGE_ROOT_DOMAIN="$root_domain"
export PYTHONDONTWRITEBYTECODE=1
/usr/bin/python3 - <<'PY'
import os
from edge_config import domain
edge = domain(os.environ["EDGE_DOMAIN"])
site = os.environ["EDGE_ROOT_DOMAIN"]
if site and (domain(site) == edge or not edge.endswith("." + site)):
    raise SystemExit("The API domain must be a subdomain of the optional website domain.")
PY
base=/etc/edge-panel
if [[ -f $base/haproxy.cfg ]]; then
  /usr/bin/python3 - "$base/haproxy.cfg" <<'PY'
import sys
from pathlib import Path
from edge_config import read_routes
try:
    read_routes(Path(sys.argv[1]).read_text())
except ValueError as error:
    raise SystemExit(str(error))
PY
fi
if [[ -f $base/panel.json ]]; then
  /usr/bin/python3 - "$base/panel.json" <<'PY'
import json, os, sys
settings = json.load(open(sys.argv[1]))
if (settings["domain"], settings["root_domain"]) != (os.environ["EDGE_DOMAIN"], os.environ["EDGE_ROOT_DOMAIN"]):
    raise SystemExit("Existing installation uses different domains.")
PY
fi
if ! systemctl is-active --quiet edge-haproxy && [[ -n $(ss -Hln 'sport = :443') ]]; then
  echo 'Port 443 is in use. Install on a separate Ubuntu host or free port 443 first.' >&2
  exit 1
fi
if [[ ! -f /etc/nginx/conf.d/edge-panel.conf ]]; then
  for port in 8081 15001; do
    if [[ -n $(ss -Hln "sport = :$port") ]]; then
      echo "Port $port is in use." >&2
      exit 1
    fi
  done
fi

apt-get update
apt-get install -y nginx haproxy certbot python3-flask gunicorn sudo
id edge-panel >/dev/null 2>&1 || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin edge-panel
install -d -m 755 "$base" /opt/edge-panel /opt/edge-panel/templates /opt/edge-panel/static /var/www/edge-panel /var/www/edge-acme
install -m 644 app.py edge_config.py apply_routes.py site_content.py /opt/edge-panel/
install -m 644 templates/*.html /opt/edge-panel/templates/
install -m 644 static/*.css /opt/edge-panel/static/
for page in web/*.html; do
  [[ -f /var/www/edge-panel/${page##*/} ]] || install -m 644 "$page" /var/www/edge-panel/
done
install -m 644 deploy/panel-proxy.conf "$base/panel-proxy.conf"
install -m 644 deploy/*.service /etc/systemd/system/
install -m 755 deploy/edge-panel-apply deploy/edge-panel-cert /usr/local/sbin/
install -d -m 755 /etc/letsencrypt/renewal-hooks/deploy
install -m 755 deploy/edge-panel-renew /etc/letsencrypt/renewal-hooks/deploy/
visudo -cf deploy/edge-panel.sudoers
install -m 440 deploy/edge-panel.sudoers /etc/sudoers.d/edge-panel

admin_password=''
show_credentials() {
  if [[ -n $admin_password ]]; then
    printf '\nUsername: admin\nPassword: %s\nSave this password. Only its hash is stored.\n' "$admin_password"
  fi
}
trap show_credentials EXIT
if [[ ! -f $base/panel.json || $rotate_admin -eq 1 ]] || ! /usr/bin/python3 -c 'import json; import sys; sys.exit(json.load(open(sys.argv[1])).get("username") != "admin")' "$base/panel.json"; then
  admin_password=$(/usr/bin/python3 - <<'PY'
import json
import os
import secrets
from pathlib import Path
from werkzeug.security import generate_password_hash
p = secrets.token_urlsafe(24)
path = Path("/etc/edge-panel/panel.json")
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps({
    "domain": os.environ["EDGE_DOMAIN"],
    "root_domain": os.environ["EDGE_ROOT_DOMAIN"],
    "password_hash": generate_password_hash(p, method="pbkdf2:sha256:600000"),
    "username": "admin",
    "secret": secrets.token_hex(32),
}, indent=2))
temp.replace(path)
print(p)
PY
)
fi
chown root:edge-panel "$base/panel.json"
chmod 640 "$base/panel.json"

site_names="$domain"
if [[ -n $root_domain ]]; then site_names="$site_names $root_domain"; fi
sed -e "s/@NAMES@/$site_names/g" -e "s/@API@/$domain/g" deploy/nginx.conf > "$base/nginx.conf"
nginx_live=/etc/nginx/conf.d/edge-panel.conf
if [[ -f $nginx_live ]]; then cp -p "$nginx_live" "$base/nginx.previous"; else rm -f "$base/nginx.previous"; fi
install -m 644 "$base/nginx.conf" "$nginx_live"
if ! nginx -t; then
  if [[ -f $base/nginx.previous ]]; then
    install -m 644 "$base/nginx.previous" "$nginx_live"
  else
    rm -f "$nginx_live"
  fi
  exit 1
fi
systemctl enable --now nginx
systemctl reload nginx

names=(-d "$domain")
if [[ -n $root_domain ]]; then names+=(-d "$root_domain"); fi
certbot certonly --webroot -w /var/www/edge-acme --cert-name edge-panel \
  "${names[@]}" --non-interactive --agree-tos --email "$email" --keep-until-expiring
/usr/local/sbin/edge-panel-cert /etc/letsencrypt/live/edge-panel

if [[ ! -f $base/haproxy.cfg ]]; then
  /usr/bin/python3 - <<'PY' > "$base/haproxy.initial"
import os
from edge_config import render
print(render(os.environ["EDGE_DOMAIN"], []), end="")
PY
  haproxy -c -f "$base/haproxy.initial"
  install -m 644 "$base/haproxy.initial" "$base/haproxy.cfg"
fi
haproxy -c -f "$base/haproxy.cfg"
systemctl daemon-reload
systemctl enable --now edge-haproxy edge-panel certbot.timer
systemctl reload edge-haproxy
systemctl restart edge-panel
systemctl is-active edge-haproxy edge-panel nginx certbot.timer
printf '\nPanel: https://%s/edge-panel/\n' "$domain"
if [[ -z $admin_password ]]; then
  printf 'Username: admin (existing password). Use --rotate-admin to generate a new one.\n'
fi
