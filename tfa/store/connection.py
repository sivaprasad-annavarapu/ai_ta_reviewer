"""The database connection, and the migrations that shape it.

Persistence is optional and its absence is never silent. With no `TFA_DB_URL`
the store reports itself unavailable, the analysis runs exactly as before, and
the screen says the run was not recorded. That matters because the alternative
— a system that quietly stops keeping the register — loses work without anyone
noticing.

A migration is applied once and then checksummed. If a file that has already
been applied is edited, the next run refuses to start: the database no longer
matches the schema the code was written against, and guessing which of the two
is right is not something a system holding a client's tax data should do.
"""
import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..config import ROOT, refresh

MIGRATIONS = Path(__file__).resolve().parent / "migrations"


class StoreError(Exception):
    """The database is configured but cannot be used as the code expects."""


# An empty TFA_DB_URL in the shell means "use the env file", which is the same
# convention as every other setting. Switching recording *off* therefore needs
# a value that says so, rather than an absence that could be an oversight.
DISABLED = {"off", "none", "no", "disabled", "-"}


def dsn() -> str:
    """The connection string, from the shell or the `env` file.

    Kept out of the manifest and out of every log line: it carries a password.
    Returns an empty string where recording is off, whether because nothing is
    configured or because it was turned off deliberately.
    """
    shell = (os.environ.get("TFA_DB_URL") or "").strip()
    if shell:
        return "" if shell.lower() in DISABLED else shell
    from ..config import _read_env_file, ENV_FILE
    values = _read_env_file(ENV_FILE)
    value = (values.get("TFA_DB_URL") or "").strip()
    return "" if value.lower() in DISABLED else value


def disabled_deliberately() -> bool:
    return (os.environ.get("TFA_DB_URL") or "").strip().lower() in DISABLED


def redacted(url: str = "") -> str:
    """What may be shown on screen: everything except the password."""
    url = url or dsn()
    if not url:
        return "(not configured)"
    return re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)


def scrub(text: str) -> str:
    """Remove any credential that a driver error may have quoted back at us.

    A database error is shown on screen and written into the run manifest. The
    manifest is the thing you hand somebody to prove how a finding was reached,
    so it must never be the place a password turns up.
    """
    if not text:
        return text
    text = re.sub(r"://([^:/@\s]+):[^@\s]*@", r"://\1:***@", text)
    return re.sub(r"(password=)\S+", r"\1***", text, flags=re.I)


def driver_available() -> bool:
    try:
        import psycopg                                    # noqa: F401
        return True
    except ImportError:
        return False


@dataclass
class Health:
    configured: bool
    driver: bool
    connected: bool
    reason: str = ""
    server: str = ""
    migrations_applied: int = 0
    migrations_pending: List[str] = None

    @property
    def enabled(self) -> bool:
        return self.configured and self.driver and self.connected

    def as_dict(self) -> Dict:
        return {"configured": self.configured, "driver": self.driver,
                "connected": self.connected, "enabled": self.enabled,
                "reason": self.reason, "server": self.server,
                "url": redacted(),
                "migrations_applied": self.migrations_applied,
                "migrations_pending": self.migrations_pending or []}


def connect(url: str = "", timeout: float = 8.0):
    """A live connection, or a StoreError saying why not."""
    url = url or dsn()
    if not url:
        if disabled_deliberately():
            raise StoreError(
                "TFA_DB_URL is set to off, so this run is deliberately not being "
                "recorded.")
        raise StoreError(
            "TFA_DB_URL is not set in the shell or in the env file, so nothing is "
            "being recorded. The analysis still runs; the register of matters and "
            "their dispositions is what is lost.")
    if not driver_available():
        raise StoreError(
            "the psycopg driver is not installed in this interpreter — "
            "pip install 'psycopg[binary]==3.2.3'")
    import psycopg
    try:
        return psycopg.connect(url, connect_timeout=int(timeout), autocommit=False)
    except Exception as exc:                            # driver-specific classes
        raise StoreError(f"{type(exc).__name__}: {exc}") from exc


def health() -> Health:
    url = dsn()
    if not url:
        return Health(False, driver_available(), False,
                      reason=("TFA_DB_URL is set to off, so nothing is being recorded"
                              if disabled_deliberately()
                              else "TFA_DB_URL is not set"))
    if not driver_available():
        return Health(True, False, False, reason="the psycopg driver is not installed")
    try:
        with connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version()")
                server = (cur.fetchone() or [""])[0].split(" on ")[0]
                applied, pending = _migration_state(cur)
            conn.rollback()
        return Health(True, True, True, server=server,
                      migrations_applied=len(applied), migrations_pending=pending)
    except StoreError as exc:
        return Health(True, True, False, reason=str(exc))


def _files() -> List[Path]:
    return sorted(MIGRATIONS.glob("*.sql"))


def _migration_state(cur) -> tuple:
    cur.execute("""SELECT to_regclass('schema_migration') IS NOT NULL""")
    if not (cur.fetchone() or [False])[0]:
        return [], [p.name for p in _files()]
    cur.execute("SELECT id, digest FROM schema_migration")
    applied = {row[0]: row[1] for row in cur.fetchall()}
    pending = []
    for path in _files():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name not in applied:
            pending.append(path.name)
        elif applied[path.name] != digest:
            raise StoreError(
                f"migration {path.name} has been edited since it was applied to this "
                f"database. Add a new migration instead of changing an applied one; "
                f"the schema in the database and the schema this code expects are no "
                f"longer the same thing.")
    return list(applied), pending


def migrate(verbose: bool = True) -> List[str]:
    """Apply what has not been applied. Idempotent, and safe to call on every
    start: it is the cheapest way to keep a schema and its code together."""
    done: List[str] = []
    with connect() as conn:
        with conn.cursor() as cur:
            try:
                _, pending = _migration_state(cur)
            except StoreError:
                conn.rollback()
                raise
            for name in pending:
                path = MIGRATIONS / name
                sql = path.read_text()
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migration (id, digest) VALUES (%s, %s) "
                    "ON CONFLICT (id) DO UPDATE SET digest = EXCLUDED.digest",
                    (name, hashlib.sha256(path.read_bytes()).hexdigest()))
                done.append(name)
                if verbose:
                    print(f"  applied {name}")
        conn.commit()
    return done
