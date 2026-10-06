#!/bin/sh
# Convenience wrapper: runs TFA under its own virtualenv.
#
# TFA is a self-contained system. It has its own interpreter, its own
# dependencies, its own knowledge files and its own database, and it borrows
# nothing from any neighbouring project — which is what makes it deployable on
# its own.
#
#   ./run.sh -m tfa.server          start the service
#   ./run.sh -m tfa.cli run ACME    analyse one client
#   ./run.sh -m tests.test_store    the test suites
set -e
DIR=$(cd "$(dirname "$0")" && pwd)
PY="$DIR/.venv/bin/python"
if [ ! -x "$PY" ]; then
  cat >&2 <<'MSG'
No virtualenv at ./.venv.

    python3.12 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt

Python 3.10 or later is required; 3.12 is what this is developed against.
MSG
  exit 1
fi
exec "$PY" "$@"
