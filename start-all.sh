#!/usr/bin/env bash
# AWF kit — wrapper 1: (re)start the local servers on the SAME ports as AWS.
#
#   localhost:<PUBLIC_PORT>  ==  https://65.0.35.71:<PUBLIC_PORT>     (what you open)
#   localhost:<APP_PORT>     ==  the server's backend port (full-stack apps)
#
# Keys: ~/GRCKEY/envs/<app>.env is exported (SERVER_* lines skipped); the app
# may also read its own gitignored .env. Apps with a database (DB=1) use the
# SERVER database through ~/GRCKEY/awf-kit/db-tunnel.sh — one DB, no local copy.
# Servers are detached (own session), so they outlive the terminal that started them.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
ROOT=$(pwd)
. deploy/app.conf
LOCAL_PORT="${LOCAL_PORT:-$PUBLIC_PORT}"
PY="$ROOT/${LOCAL_VENV:-.venv}/bin/python"
mkdir -p logs .awf
[[ -x "$PY" ]] || { echo "✗ No venv at ${LOCAL_VENV:-.venv} — run: python3 awf.py 5"; exit 1; }
NODE_BIN=$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -1 || true)
[[ -n "$NODE_BIN" ]] && export PATH="$NODE_BIN:$PATH"

KEYS="$HOME/GRCKEY/envs/$APP.env"
if [[ -f "$KEYS" ]]; then
    # A private temp file, not `. <(…)`: macOS bash 3.2 silently sources nothing from <(…).
    KTMP=$(mktemp); chmod 600 "$KTMP"
    grep -E '^[A-Za-z_][A-Za-z0-9_]*=' "$KEYS" | grep -v '^SERVER_' > "$KTMP"
    set -a; . "$KTMP"; set +a; rm -f "$KTMP"
fi

if [[ "${DB:-0}" == 1 ]]; then
    "$HOME/GRCKEY/awf-kit/db-tunnel.sh" --bg || { echo "✗ No tunnel to the server database"; exit 1; }
fi

./stop-all.sh >/dev/null 2>&1 || true

spawn() {  # spawn <pidfile> <logfile> <cmd…>: fork, setsid, exec — detached for good
    python3 - "$@" <<'PYS'
import os, sys
pidfile, log, argv = sys.argv[1], sys.argv[2], sys.argv[3:]
pid = os.fork()
if pid:
    open(pidfile, "w").write(f"{pid}\n"); os._exit(0)
os.setsid()
null = os.open(os.devnull, os.O_RDONLY); out = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(null, 0); os.dup2(out, 1); os.dup2(out, 2)
os.execvp(argv[0], argv)
PYS
}

echo "▸ $APP — starting on the AWS port numbers"
# shellcheck disable=SC2086
(cd "${LOCAL_WORKDIR:-.}" && PYTHONUNBUFFERED=1 spawn "$ROOT/.awf/server.pid" "$ROOT/logs/server.log" "$PY" $LOCAL_START)
if [[ -n "${LOCAL_FRONTEND_START:-}" ]]; then
    (cd frontend && spawn "$ROOT/.awf/frontend.pid" "$ROOT/logs/frontend.log" sh -c "$LOCAL_FRONTEND_START")
fi

BACK="http://127.0.0.1:${LOCAL_BACKEND_PORT:-$LOCAL_PORT}${HEALTH_PATH:-/}"
for _ in $(seq 1 90); do
    if curl -s -o /dev/null -m 120 -X "${HEALTH_METHOD:-GET}" -f "$BACK" && curl -s -o /dev/null -f "http://127.0.0.1:$LOCAL_PORT/"; then
        echo "✓ up — http://localhost:$LOCAL_PORT   (AWS: https://65.0.35.71:$PUBLIC_PORT)"
        [[ "${DB:-0}" == 1 ]] && echo "  database: SERVER, via 127.0.0.1:55432"
        echo "  logs: logs/server.log${LOCAL_FRONTEND_START:+ · logs/frontend.log}"
        exit 0
    fi
    kill -0 "$(cat .awf/server.pid)" 2>/dev/null || { echo "✗ backend exited during startup:"; tail -20 logs/server.log; exit 1; }
    sleep 1
done
echo "✗ not answering after 90s"; tail -15 logs/*.log; exit 1
