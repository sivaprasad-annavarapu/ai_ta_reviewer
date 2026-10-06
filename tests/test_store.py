"""The register must survive a re-run.

This is the acceptance test for persistence, and it tests the only thing that
is genuinely hard about it: what happens to a decision a person recorded when
the filings change underneath it.

The sequence exercised here is the real one:

  1. a run raises a matter;
  2. a person accepts it, with a note;
  3. the client sends a corrected return, and the same rule now reports
     different figures — the accepted matter must be superseded, not silently
     reused, and the note must survive against the figures it was about;
  4. the correction is withdrawn, and the original matter must come back as the
     same matter, with its history intact.

    ./run.sh -m tests.test_store
"""
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tfa.ai.client import Budget, ModelClient
from tfa.config import refresh
from tfa.pipeline import analyse
from tfa.store import add_disposition, health, matter_detail, migrate, register
from tfa.store.connection import connect
from tfa.store.matters import matter_key

FAILURES = []
CLIENT = "ACME-PVT-LTD"

# The test needs a database it is allowed to destroy. It makes one, uses it,
# and drops it. An earlier version of this file truncated whatever TFA_DB_URL
# happened to point at, which on a working machine is the firm's own register —
# so the guard below is not paranoia, it is a bug that was already paid for.
SCRATCH = "tfa_selftest"


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def quiet():
    """No model calls: the judgment rules report as not assessed, and the
    deterministic register is what is under test."""
    return ModelClient(refresh(), Budget(max_calls=0))


def _database_of(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.path or "").lstrip("/")


def _with_database(url: str, name: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path="/" + name))


def make_scratch(base_url: str) -> str:
    """A database of the test's own, dropped when it finishes.

    Postgres will not drop a database anyone is connected to, so this connects
    to the server's default database to do the housekeeping.
    """
    import psycopg
    admin = _with_database(base_url, _database_of(base_url) or "postgres")
    with psycopg.connect(admin, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
            cur.execute(f'CREATE DATABASE "{SCRATCH}"')
    return _with_database(base_url, SCRATCH)


def drop_scratch(base_url: str) -> None:
    import psycopg
    admin = _with_database(base_url, _database_of(base_url) or "postgres")
    try:
        with psycopg.connect(admin, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
    except Exception as exc:
        print(f"  note: the scratch database was left behind ({exc})")


def guard() -> None:
    """Refuse to touch anything that is not the scratch database."""
    from tfa.store.connection import dsn
    name = _database_of(dsn())
    if name != SCRATCH:
        raise SystemExit(
            f"refusing to run: this test destroys data, and TFA_DB_URL points at "
            f"{name!r} rather than the scratch database {SCRATCH!r}.")


def wipe() -> None:
    guard()
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                TRUNCATE disposition, enquiry, finding, fact_cell, run_filing,
                         matter, run, filing_blob, filing, client CASCADE
            """)
        conn.commit()


def run_against(folder: Path):
    return analyse(CLIENT, filings_root=folder, client=quiet())


def main() -> int:
    state = health()
    if not state.enabled:
        print(f"no database: {state.reason}")
        print("  ./run.sh -m tfa.store.local start   then put the URL in env")
        return 2

    from tfa.store.connection import dsn
    base = dsn()
    if _database_of(base) == SCRATCH:
        print("already pointed at the scratch database")
        scratch = base
    else:
        print(f"the firm's register is at {_database_of(base)!r} and is left alone;")
        print(f"this test works in {SCRATCH!r} and drops it when it finishes.")
        scratch = make_scratch(base)
    # Every store function reads TFA_DB_URL, and the shell wins over the file,
    # so setting it here redirects the whole run without touching env.
    os.environ["TFA_DB_URL"] = scratch
    guard()
    migrate(verbose=False)
    wipe()

    work = Path(tempfile.mkdtemp(prefix="tfa-store-test-"))
    folder = work / CLIENT
    shutil.copytree(ROOT / "filings" / CLIENT, folder)

    # ---- 1. the first run --------------------------------------------------
    print("\nfirst run")
    first = run_against(folder)
    p = first.manifest["persistence"]
    check("the run is recorded", p["recorded"], p.get("reason", ""))
    counts = p["register"]["counts"]
    check("every matter is new on a first run",
          counts["again"] == 0 and counts["new"] > 0, json.dumps(counts))

    book = register(CLIENT)
    check("the register reads back", book["available"] and bool(book["matters"]))
    raised = len([f for f in first.findings if f.verdict == "FAIL"])
    check(f"one matter per raised finding ({len(book['matters'])} of {raised})",
          len(book["matters"]) == raised)
    check("an enquiry is queued for each matter that has one",
          len(book["enquiries"]) > 0)

    # the matter to decide about: the turnover tie-out for AY 2024-25
    target = next((m for m in book["matters"]
                   if m["rule_id"] == "TIE-001" and m["ay"] == "AY 2024-25"), None)
    if target is None:
        check("the turnover tie-out is on the register", False)
        return 1
    check("the turnover tie-out is on the register", True)

    # ---- 2. a person decides ----------------------------------------------
    print("\na decision is recorded")
    outcome = add_disposition(target["matter_key"], "accept", "RB",
                              "Agreed — client to reconcile the 1.24 crore and "
                              "produce the credit notes.")
    check("the decision is accepted", outcome.get("ok"), outcome.get("reason", ""))
    detail = matter_detail(target["matter_key"])
    check("the matter is now accepted", detail["matter"]["state"] == "accepted")
    check("the decision is on record with its author",
          detail["decisions"] and detail["decisions"][0]["actor"] == "RB")

    # ---- 3. the client sends a corrected return ---------------------------
    print("\nthe figures change underneath it")
    itr = folder / "AY2024-25" / "ITR6_2024-25.json"
    doc = json.loads(itr.read_text())
    was = doc["ITR"]["ITR6"]["TradingAccount"]["TotRevenueFrmOperations"]
    now = was + 3_000_000            # still wrong, but wrong by a different amount
    for field in ("SaleOfGoods", "SalesGrossReceiptsTotal", "TotRevenueFrmOperations"):
        doc["ITR"]["ITR6"]["TradingAccount"][field] = now
    doc["ITR"]["ITR6"]["PartA_PL"]["CreditsToPL"]["TotRevenueFromOperations"] = now
    itr.write_text(json.dumps(doc, indent=1))

    second = run_against(folder)
    reg2 = second.manifest["persistence"]["register"]
    check("the corrected figures raise a new matter", reg2["counts"]["new"] >= 1,
          json.dumps(reg2["counts"]))
    superseded = [s["old"] for s in reg2["superseded"]]
    check("the decided matter is superseded, not reused",
          target["matter_key"] in superseded,
          f"superseded: {superseded}")

    detail = matter_detail(target["matter_key"])
    check("the superseded matter keeps its state on the record",
          detail["matter"]["state"] == "superseded")
    check("the note survives against the figures it was about",
          any(d["note"] and "credit notes" in d["note"]
              for d in detail["decisions"]))
    check("the replacement is named on the old matter",
          bool(detail["matter"]["superseded_by"]))

    replacement = detail["matter"]["superseded_by"]
    fresh = matter_detail(replacement)
    check("the replacement carries the new figures, undecided",
          fresh["matter"]["state"] == "open")
    check("the replacement has no decision of its own", not fresh["decisions"])

    # ---- 4. the correction is withdrawn ------------------------------------
    print("\nthe correction is withdrawn")
    doc["ITR"]["ITR6"]["TradingAccount"]["SaleOfGoods"] = was
    doc["ITR"]["ITR6"]["TradingAccount"]["SalesGrossReceiptsTotal"] = was
    doc["ITR"]["ITR6"]["TradingAccount"]["TotRevenueFrmOperations"] = was
    doc["ITR"]["ITR6"]["PartA_PL"]["CreditsToPL"]["TotRevenueFromOperations"] = was
    itr.write_text(json.dumps(doc, indent=1))

    third = run_against(folder)
    reg3 = third.manifest["persistence"]["register"]
    check("the original matter is recognised again",
          target["matter_key"] in reg3["again"] or
          target["matter_key"] in reg3["reopened"],
          json.dumps(reg3["counts"]))
    detail = matter_detail(target["matter_key"])
    check("it is reopened rather than duplicated",
          detail["matter"]["state"] == "open")
    check("the whole history is intact",
          len(detail["decisions"]) >= 3 and
          any(d["actor"] == "RB" for d in detail["decisions"]))
    check("the run history shows three runs",
          len(register(CLIENT)["runs"]) == 3)

    # ---- 5. nothing was written twice --------------------------------------
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*), count(DISTINCT matter_key) FROM matter")
            total, distinct = cur.fetchone()
            check("no matter is duplicated", total == distinct)
            cur.execute("""SELECT count(*) FROM enquiry e
                            GROUP BY matter_key ORDER BY 1 DESC LIMIT 1""")
            most = (cur.fetchone() or [0])[0]
            check("a matter is never asked about twice", most == 1, f"max {most}")
            cur.execute("SELECT count(*) FROM run")
            check("three runs on record", cur.fetchone()[0] == 3)
        conn.rollback()

    shutil.rmtree(work, ignore_errors=True)
    os.environ.pop("TFA_DB_URL", None)
    drop_scratch(base)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} promise(s) broken: {', '.join(FAILURES)}")
        return 1
    print("the register survives a re-run")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
