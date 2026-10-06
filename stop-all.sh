#!/usr/bin/env bash
# AWF kit — wrapper 2: stop the local servers. Waits for the ports to free.
# Never touches the DB tunnel (other apps may share it).
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
. deploy/app.conf
PORTS="${LOCAL_PORT:-$PUBLIC_PORT} ${LOCAL_BACKEND_PORT:-}"
pids="$(cat .awf/*.pid 2>/dev/null)"
for p in $PORTS; do pids="$pids $(lsof -ti tcp:"$p" -sTCP:LISTEN 2>/dev/null)"; done
pids=$(echo $pids | tr ' ' '\n' | sort -u | tr '\n' ' ')
if [[ -z "${pids// /}" ]]; then echo "not running"; rm -f .awf/*.pid; exit 0; fi
kill $pids 2>/dev/null || true
for _ in $(seq 1 10); do
    busy=0; for p in $PORTS; do lsof -ti tcp:"$p" -sTCP:LISTEN >/dev/null 2>&1 && busy=1; done
    [[ $busy == 0 ]] && { rm -f .awf/*.pid; echo "✓ stopped"; exit 0; }
    sleep 1
done
kill -9 $pids 2>/dev/null || true; rm -f .awf/*.pid; echo "✓ stopped (forced)"
