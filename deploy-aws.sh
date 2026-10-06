#!/usr/bin/env bash
# AWF kit — wrapper 12: deploy THIS working tree straight to AWS.
# Generic: everything app-specific is in deploy/app.conf.
# Source of truth: ~/GRCKEY/awf-kit/ (copy, don't fork). Rules: ~/GRCKEY/AWF_CONVENTIONS.md
#
# Usage: ./deploy-aws.sh        (normally via: python3 awf.py 12)
#
# Not routed through GitHub: rsync from this machine, so what was tested locally
# is what runs. Keys go from ~/GRCKEY/envs/<app>.env to the server's .env over
# stdin. Lines "SERVER_<KEY>=v" in that file become "<KEY>=v" on the server
# (e.g. a DB URL that differs between laptop and server).
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=deploy/app.conf
. deploy/app.conf

SSH_KEY="${AWF_SSH_KEY:-$HOME/GRCKEY/anyaudit_ai_dev_pem_key 2.pem}"
SSH_USER="${AWF_SSH_USER:-ubuntu}"
SSH_HOST="${AWF_SSH_HOST:-65.0.35.71}"
ENV_FILE="${AWF_ENV_FILE:-$HOME/GRCKEY/envs/$APP.env}"
STAGE="/home/$SSH_USER/$APP-stage"
LIVE="/var/www/$APP"

BOLD=$'\033[1m'; RED=$'\033[31m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'
say()  { echo "${BOLD}$*${RESET}"; }
ok()   { echo "${GREEN}✓${RESET} $*"; }
warn() { echo "${YELLOW}!${RESET} $*"; }
fail() { echo "${RED}✗ $*${RESET}" >&2; exit 1; }
remote() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 "$SSH_USER@$SSH_HOST" "$@"; }

# ── Before touching anything ─────────────────────────────────────────────────
[[ -f "$SSH_KEY" ]] || fail "No SSH key at: $SSH_KEY (set AWF_SSH_KEY)"
PERMS=$(stat -f "%Lp" "$SSH_KEY" 2>/dev/null || stat -c "%a" "$SSH_KEY")
[[ "$PERMS" == 600 || "$PERMS" == 400 ]] || fail "Key permissions are $PERMS. Run: chmod 600 \"$SSH_KEY\""
[[ -f "$ENV_FILE" ]] || fail "No env file at: $ENV_FILE"
for k in $REQUIRED_KEYS; do
    grep -qE "^$k=.{20,}" "$ENV_FILE" || fail "$k is missing or empty in $ENV_FILE"
done
ok "Keys present in ${ENV_FILE/#$HOME/~} ($REQUIRED_KEYS)"
if [[ -n "${PREFLIGHT_CMD:-}" ]]; then
    (eval "$PREFLIGHT_CMD") || fail "Preflight failed: $PREFLIGHT_CMD"
    ok "Preflight: $PREFLIGHT_CMD"
fi
remote true 2>/dev/null || fail "Cannot reach $SSH_USER@$SSH_HOST over SSH."
ok "Reached $SSH_HOST"

REV="$( (git rev-parse --short HEAD 2>/dev/null || echo nogit) )"
[[ -n "$(git status --porcelain 2>/dev/null)" ]] && REV="$REV+dirty"
echo "$REV $(date -u +%Y-%m-%dT%H:%M:%SZ) $(whoami)@$(hostname -s)" > DEPLOYED_REVISION
if [[ -n "${FRONTEND_BUILD:-}" ]]; then
    say "Building the frontend: $FRONTEND_BUILD"
    (eval "$FRONTEND_BUILD") >/dev/null || fail "Frontend build failed — run it by hand to see why: $FRONTEND_BUILD"
    ok "Frontend built"
fi
say "Deploying $APP $REV → $SSH_USER@$SSH_HOST:$LIVE  (https://$SSH_HOST:$PUBLIC_PORT)"
echo

# ── 1. Code → stage ──────────────────────────────────────────────────────────
say "[1/4] Uploading code"
EXCL=(--exclude '.git/' --exclude '.venv/' --exclude 'venv/' --exclude '__pycache__/' --exclude '*.pyc'
      --exclude '/env' --exclude '.env' --exclude '.env.*' --exclude '.DS_Store' --exclude '.claude/'
      --exclude 'logs/' --exclude '.awf/' --exclude '.pytest_cache/' --exclude 'node_modules/')
# SHIP_TOP: allow-list of top-level entries; everything else at the root is
# excluded by name (openrsync ignores a bare "/*", so it is spelled out).
if [[ -n "${SHIP_TOP:-}" ]]; then
    shopt -s dotglob nullglob
    for e in *; do [[ " $SHIP_TOP " == *" $e "* ]] || EXCL+=(--exclude "/$e"); done
    shopt -u dotglob nullglob
fi
INCL=()
for p in $SHIP_ONLY; do INCL+=(--include "$p"); done
for p in $EXCLUDES; do EXCL+=(--exclude "$p"); done
rsync -az --delete -e "ssh -i '$SSH_KEY' -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15" \
    "${INCL[@]+"${INCL[@]}"}" "${EXCL[@]}" \
    ./ "$SSH_USER@$SSH_HOST:$STAGE/" || fail "rsync failed"
rm -f DEPLOYED_REVISION
ok "Code staged at $STAGE"

# ── 2. Keys → server .env over stdin ─────────────────────────────────────────
say "[2/4] Uploading keys"
python3 - "$ENV_FILE" <<'PY' | remote "sudo mkdir -p $LIVE && sudo tee $LIVE/.env >/dev/null && sudo chmod 600 $LIVE/.env" || fail "Could not write $LIVE/.env"
import sys
lines = open(sys.argv[1]).read().splitlines()
over = {}
for l in lines:
    if l.startswith("SERVER_") and "=" in l:
        k, _, v = l[7:].partition("="); over[k] = v
out = []
for l in lines:
    k = l.split("=", 1)[0].strip()
    if k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY") or l.startswith("SERVER_"):
        continue                      # local-only: server uses its instance role
    out.append(f"{k}={over.pop(k)}" if k in over and "=" in l else l)
out += [f"{k}={v}" for k, v in over.items()]
print("\n".join(out))
PY
ok "$LIVE/.env written (mode 600, AWS keys stripped, SERVER_ overrides applied)"

# ── 3. Install / restart on the server ───────────────────────────────────────
say "[3/4] Installing on the server"
remote "sudo STAGE=$STAGE bash $STAGE/deploy/remote-install.sh" || fail "Server install failed (see above)."

# ── 4. Prove it works through nginx, with certificate checking on ────────────
say "[4/4] Verifying"
remote "bash -s" <<VERIFY || fail "Deployed, but verification failed. Logs: python3 awf.py 13"
set -uo pipefail
R="--resolve $SSH_HOST:$PUBLIC_PORT:127.0.0.1"
URL="https://$SSH_HOST:$PUBLIC_PORT"
for i in \$(seq 1 60); do
    code=\$(curl -s \$R -o /dev/null -w '%{http_code}' "\$URL/")
    [[ "\$code" == 200 ]] && break; sleep 2
done
[[ "\$code" == 200 ]] || { echo "  ✗ \$URL/ answered \$code"; sudo journalctl -u $APP -n 30 --no-pager; exit 1; }
echo "  ✓ page served through nginx (\$URL/)"
# The backend may still be booting after the restart (502 from nginx): retry.
for i in \$(seq 1 30); do
    out=\$(curl -s \$R -m 300 -X ${HEALTH_METHOD:-GET} -w '\n%{http_code}' "\$URL$HEALTH_PATH")
    code=\${out##*\$'\n'}
    [[ "\$code" == 200 ]] && break; sleep 2
done
[[ "\$code" == 200 ]] || { echo "  ✗ $HEALTH_PATH answered \$code: \${out:0:300}"; sudo journalctl -u $APP -n 30 --no-pager; exit 1; }
echo "  ✓ $HEALTH_PATH: \$(echo "\${out%\$'\n'*}" | head -c 300)"
echo "  ✓ memory: \$(systemctl show $APP -p MemoryCurrent --value | awk '{printf "%.0f MB", \$1/1048576}')"
VERIFY

PUBLIC="https://$SSH_HOST:$PUBLIC_PORT"
if curl -s -m 8 -o /dev/null "$PUBLIC/"; then
    ok "Reachable from here with a valid certificate: $PUBLIC"
else
    warn "$PUBLIC is not reachable from this machine — open TCP $PUBLIC_PORT in the AWS security group."
fi
echo
ok "Deployed $APP $REV. Live: $PUBLIC"
