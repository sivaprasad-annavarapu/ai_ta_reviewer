#!/usr/bin/env bash
# AWF kit — server half of the deploy. Run by deploy-aws.sh as:
#     sudo STAGE=~/<app>-stage bash ~/<app>-stage/deploy/remote-install.sh
#
# Idempotent. Touches only this app's own things: /var/www/<app>, the <app>
# system user, <app>.service, the nginx site '<app>', and (DB=1) the <app>
# database + role. Never drops data, never edits another site, reloads nginx
# only after nginx -t passes with its own changed vhost.
set -euo pipefail
: "${STAGE:?}"
# shellcheck source=app.conf
. "$STAGE/deploy/app.conf"
LIVE="/var/www/$APP"
PUBLIC_IP=65.0.35.71
# One Let's Encrypt IP certificate serves every AA agent port (9001, 9002, …).
# It is owned and renewed by aikosh (aikosh-cert.timer). One cert, not one per
# app: LE allows only 5 duplicate certificates per identifier per week, and
# 6-day IP certificates renew every ~3 days.
CERT_DIR=/etc/lego-aikosh/certificates
UPPER="${DB_PREFIX:-$(echo "$APP" | tr '[:lower:]' '[:upper:]')}"   # env-key prefix: <PREFIX>_DB_NAME …
REQUIREMENTS="${REQUIREMENTS:-requirements.txt}"
WORKDIR="${WORKDIR:-.}"
ENV_MODE="${ENV_MODE:-var}"          # var: <ENV_VAR>=path to .env | file: systemd EnvironmentFile
NGINX_TEMPLATE="${NGINX_TEMPLATE:-nginx.conf.tmpl}"

say() { echo; echo "── $* ──"; }
[[ $EUID -eq 0 ]] || { echo "run with sudo" >&2; exit 1; }

say "Ports"
if ss -ltnpH "sport = :$PUBLIC_PORT" | grep -v nginx | grep -q .; then
    echo "ERROR: :$PUBLIC_PORT is held by something other than nginx" >&2; exit 1
fi
if ss -ltnpH "sport = :$APP_PORT" | grep -q . && ! systemctl is-active --quiet "$APP"; then
    echo "ERROR: :$APP_PORT is in use and it is not $APP.service" >&2; exit 1
fi
echo "  :$PUBLIC_PORT and :$APP_PORT are free for $APP"

say "User and tree"
id -u "$APP" >/dev/null 2>&1 || useradd --system --home "$LIVE" --shell /usr/sbin/nologin "$APP"
mkdir -p "$LIVE/logs"
# The app user's home is $LIVE, so its caches (.cache/ .local/ .config/) live here too.
PRESERVE=(--exclude '.venv/' --exclude '.env' --exclude 'logs/' --exclude '/.cache/' --exclude '/.local/' --exclude '/.config/')
for p in $RUNTIME_DIRS; do PRESERVE+=(--exclude "$p"); mkdir -p "$LIVE/$p"; done
for p in ${RUNTIME_FILES:-}; do PRESERVE+=(--exclude "$p"); done   # files the app writes; never overwritten
for p in ${SEED_FILES:-}; do [[ -e "$LIVE/$p" ]] || cp -a "$STAGE/$p" "$LIVE/$p" 2>/dev/null || true; done   # first deploy only
rsync -a --delete "${PRESERVE[@]}" "$STAGE/" "$LIVE/"
[[ -f "$LIVE/.env" ]] || { echo "ERROR: $LIVE/.env missing" >&2; exit 1; }
chown -R "$APP:$APP" "$LIVE"
chmod 600 "$LIVE/.env"
echo "  code at $(cat "$LIVE/DEPLOYED_REVISION" 2>/dev/null || echo '?')"

if [[ -n "${APT_PACKAGES:-}" ]]; then
    say "System packages"
    MISSING=""
    for p in $APT_PACKAGES; do dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"; done
    if [[ -n "$MISSING" ]]; then
        echo "  installing:$MISSING"
        DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get update -qq >/dev/null
        DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y -qq $MISSING >/dev/null
    else
        echo "  present: $APT_PACKAGES"
    fi
fi

say "Python"
if [[ ! -x "$LIVE/.venv/bin/python" ]]; then
    dpkg -s python3.12-venv >/dev/null 2>&1 || {
        DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get update -qq >/dev/null
        DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=l apt-get install -y -qq python3.12-venv >/dev/null; }
    sudo -u "$APP" python3 -m venv "$LIVE/.venv"
    sudo -u "$APP" "$LIVE/.venv/bin/pip" install -q --upgrade pip
fi
REQ_SHA=$(sha256sum "$LIVE/$REQUIREMENTS" | cut -d' ' -f1)
if [[ "$(cat "$LIVE/.venv/.req-sha" 2>/dev/null)" != "$REQ_SHA" ]]; then
    echo "  requirements.txt changed — installing"
    sudo -u "$APP" "$LIVE/.venv/bin/pip" install -q \
        --extra-index-url https://download.pytorch.org/whl/cpu -r "$LIVE/$REQUIREMENTS"
    echo "$REQ_SHA" > "$LIVE/.venv/.req-sha"; chown "$APP:$APP" "$LIVE/.venv/.req-sha"
else
    echo "  unchanged — skipped"
fi

if [[ "${DB:-0}" == 1 ]]; then
    say "Database"
    # From the uploaded .env; given to psql on stdin, never in argv.
    DB_NAME=$(grep -m1 "^${UPPER}_DB_NAME=" "$LIVE/.env" | cut -d= -f2-)
    DB_USER=$(grep -m1 "^${UPPER}_DB_USER=" "$LIVE/.env" | cut -d= -f2-)
    DB_PW=$(grep -m1 "^${UPPER}_DB_PASSWORD=" "$LIVE/.env" | cut -d= -f2-)
    [[ -n "$DB_NAME" && -n "$DB_USER" && -n "$DB_PW" ]] || { echo "ERROR: ${UPPER}_DB_NAME/_USER/_PASSWORD missing in .env" >&2; exit 1; }
    sudo -u postgres psql -qAt -v ON_ERROR_STOP=1 <<SQL
DO \$\$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '$DB_USER') THEN
    CREATE ROLE "$DB_USER" LOGIN PASSWORD '$DB_PW';
  ELSE
    ALTER ROLE "$DB_USER" PASSWORD '$DB_PW';
  END IF;
END \$\$;
SQL
    if ! sudo -u postgres psql -qAtc "SELECT 1 FROM pg_database WHERE datname = '$DB_NAME'" | grep -q 1; then
        sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
        echo "  created database $DB_NAME (owner $DB_USER)"
    else
        echo "  database $DB_NAME exists — left as is"
    fi
    unset DB_PW
fi

if [[ -n "${POST_INSTALL:-}" ]]; then
    say "Post-install"
    # As the app user, in its working dir, with its .env loaded (e.g. seed a DB if absent).
    sudo -u "$APP" bash -c "set -euo pipefail; cd '$LIVE/$WORKDIR'; set -a; . '$LIVE/.env'; set +a; $POST_INSTALL" \
        2>&1 | tail -5 | sed -E 's/([Pp]assword[^:=]*[:=] *).*/\1<hidden — see ~\/GRCKEY\/envs>/' | sed 's/^/  /'
fi

chmod 644 "/etc/systemd/system/$APP.service" "/etc/nginx/sites-available/$APP" 2>/dev/null || true

if [[ "${CERT_OWNER:-0}" == 1 ]]; then
    say "Shared AA certificate (this app owns its renewal)"
    install -m 755 "$LIVE/deploy/aa-cert/renew-cert" "/usr/local/sbin/$APP-renew-cert"
    for u in service timer; do
        if ! cmp -s "$LIVE/deploy/aa-cert/cert.$u" "/etc/systemd/system/$APP-cert.$u"; then
            install -m 644 "$LIVE/deploy/aa-cert/cert.$u" "/etc/systemd/system/$APP-cert.$u"; RELOAD=1
        fi
    done
    [[ "${RELOAD:-0}" == 1 ]] && systemctl daemon-reload
    systemctl enable --now "$APP-cert.timer" >/dev/null 2>&1
    "/usr/local/sbin/$APP-renew-cert" 2>&1 | tail -2 | sed 's/^/  /'
fi

say "Service"
if [[ "$ENV_MODE" == file ]]; then ENVLINE="EnvironmentFile=$LIVE/.env"
else ENVLINE="Environment=$ENV_VAR=$LIVE/.env"; fi
UNIT=$(mktemp)
sed -e "s|__APP__|$APP|g" -e "s|__TITLE__|$TITLE|g" -e "s|__APP_PORT__|$APP_PORT|g" \
    -e "s|__PUBLIC_PORT__|$PUBLIC_PORT|g" -e "s|__START__|$SERVER_START|g" \
    -e "s|__ENVLINE__|$ENVLINE|g" -e "s|__WORKDIR__|$WORKDIR|g" -e "s|__MEMMAX__|${MEMORY_MAX:-1G}|g" \
    "$LIVE/deploy/app.service.tmpl" > "$UNIT"
if ! cmp -s "$UNIT" "/etc/systemd/system/$APP.service"; then
    install -m 644 "$UNIT" "/etc/systemd/system/$APP.service"
    systemctl daemon-reload
    systemctl enable "$APP" >/dev/null 2>&1
    echo "  unit installed"
fi
rm -f "$UNIT"
systemctl restart "$APP"
echo "  restarted"

say "nginx :$PUBLIC_PORT (Let's Encrypt IP certificate)"
[[ -f "$CERT_DIR/$PUBLIC_IP.crt" && -f "$CERT_DIR/$PUBLIC_IP.key" ]] || {
    echo "ERROR: no certificate in $CERT_DIR — deploy aikosh first (it owns and renews the AA IP cert)" >&2; exit 1; }
echo "  $(openssl x509 -in "$CERT_DIR/$PUBLIC_IP.crt" -noout -issuer -enddate | tr '\n' ' ')"
NEW=$(mktemp)
sed -e "s|__APP__|$APP|g" -e "s|__APP_PORT__|$APP_PORT|g" -e "s|__PUBLIC_PORT__|$PUBLIC_PORT|g" \
    -e "s|__PUBLIC_IP__|$PUBLIC_IP|g" -e "s|__CERT_DIR__|$CERT_DIR|g" \
    -e "s|__STATIC_DIR__|$LIVE/${STATIC_DIR:-}|g" -e "s|__API_PREFIX__|${API_PREFIX:-/api/}|g" \
    "$LIVE/deploy/$NGINX_TEMPLATE" > "$NEW"
if ! cmp -s "$NEW" "/etc/nginx/sites-available/$APP"; then
    [[ -f "/etc/nginx/sites-available/$APP" ]] && cp "/etc/nginx/sites-available/$APP" "/etc/nginx/sites-available/$APP.backup-$(date +%Y%m%d-%H%M%S)"
    install -m 644 "$NEW" "/etc/nginx/sites-available/$APP"
    ln -sf "/etc/nginx/sites-available/$APP" "/etc/nginx/sites-enabled/$APP"
    if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx
        echo "  vhost installed, nginx reloaded"
    else
        echo "ERROR: nginx -t failed with the new $APP vhost — removing it" >&2
        nginx -t 2>&1 | tail -3 >&2
        rm -f "/etc/nginx/sites-enabled/$APP"
        nginx -t >/dev/null 2>&1 && systemctl reload nginx
        exit 1
    fi
else
    echo "  unchanged"
fi
rm -f "$NEW"
