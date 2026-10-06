"""A local Postgres for development, and nothing more.

`pgserver` ships a real Postgres in a wheel, so a working database needs no
system installation and no server anyone has to look after. That is worth
having for development and for a single-machine install, and it is emphatically
not a production deployment: one process, one machine, no backups, no failover.

The data directory sits outside the repository on purpose. It holds a client's
tax filings, and a database file inside a working tree gets committed,
archived, copied and emailed by accident.

    python3 -m tfa.store.local start      # start it and print the URL
    python3 -m tfa.store.local status
    python3 -m tfa.store.local stop
"""
import os
import sys
from pathlib import Path

DATA_DIR = Path(os.environ.get("TFA_PGDATA")
                or (Path.home() / ".tfa" / "pgdata")).expanduser()
DATABASE = os.environ.get("TFA_PGDATABASE", "tfa")


def _server():
    try:
        import pgserver
    except ImportError:
        raise SystemExit(
            "pgserver is not installed in this interpreter.\n"
            "  pip install pgserver        (development only)\n"
            "Or point TFA_DB_URL at a Postgres you already have.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.chmod(0o700)
    return pgserver.get_server(str(DATA_DIR), cleanup_mode=None)


def _database_exists(server) -> bool:
    """`psql` writes "already exists" to its error stream and still returns
    successfully, so a CREATE whose failure is caught never fires and every
    start claims to have created a database that has been there for weeks.
    Ask instead."""
    out = server.psql(
        f"SELECT count(*) FROM pg_database WHERE datname = '{DATABASE}'")
    digits = [line.strip() for line in out.splitlines() if line.strip().isdigit()]
    return bool(digits) and digits[0] != "0"


def start(quiet: bool = False) -> str:
    """Start it if it is not running, create the database, return the URL.

    The existence check is a query rather than a CREATE whose failure is
    ignored: `psql` reports "already exists" on its error stream and returns
    successfully, so catching the exception never fired and every start claimed
    to have created a database that had been there for weeks.
    """
    server = _server()
    url = server.get_uri(DATABASE)
    if _database_exists(server):
        if not quiet:
            print(f"database {DATABASE} is already there")
    else:
        server.psql(f'CREATE DATABASE "{DATABASE}"')
        if not quiet:
            print(f"created database {DATABASE}")
    if not quiet:
        print(f"data directory  {DATA_DIR}")
        print(f"TFA_DB_URL={url}")
    return url


def status() -> int:
    if not DATA_DIR.exists():
        print(f"no local database yet ({DATA_DIR} does not exist)")
        return 1
    try:
        import pgserver                                      # noqa: F401
        server = _server()
        print(f"running · {DATA_DIR}")
        print(server.psql("SELECT version()").strip().splitlines()[-2].strip())
        return 0
    except SystemExit:
        raise
    except Exception as exc:
        print(f"not running: {type(exc).__name__}: {exc}")
        return 1


def stop() -> int:
    try:
        import pgserver
    except ImportError:
        print("pgserver is not installed")
        return 1
    server = pgserver.get_server(str(DATA_DIR), cleanup_mode=None)
    server.cleanup()
    print("stopped")
    return 0


def main() -> int:
    command = sys.argv[1] if len(sys.argv) > 1 else "start"
    if command == "start":
        start()
        return 0
    if command == "status":
        return status()
    if command == "stop":
        return stop()
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
