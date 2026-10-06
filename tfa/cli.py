"""Headless runs, for a terminal or a cron job.

    python3 -m tfa.cli list
    python3 -m tfa.cli run ACME-PVT-LTD
    python3 -m tfa.cli run ACME-PVT-LTD --no-ai --json out/acme.json
    python3 -m tfa.cli rules
    python3 -m tfa.cli matrix ACME-PVT-LTD
    python3 -m tfa.cli db --migrate
    python3 -m tfa.cli register ACME-PVT-LTD --state open
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tfa.ai.client import Budget, ModelClient                    # noqa: E402
from tfa.config import refresh                                   # noqa: E402
from tfa.facts.extract import load_fact_map                      # noqa: E402
from tfa.foundation.authority import default_pack_path, load_pack  # noqa: E402
from tfa.pipeline import analyse, make_client                    # noqa: E402
from tfa.report.narrative import synthesise                      # noqa: E402
from tfa.report.scoring import score_run                         # noqa: E402
from tfa.rules.context import money                              # noqa: E402
from tfa.rules.registry import default_rules_path, load_rules    # noqa: E402
from tfa.store import health as store_health, migrate as store_migrate  # noqa: E402
from tfa.store import register as store_register                # noqa: E402


def cmd_list(args) -> int:
    settings = refresh()
    root = settings.filings_dir
    if not root.is_dir():
        print(f"no filings folder at {root}")
        return 1
    folders = [p for p in sorted(root.iterdir()) if p.is_dir()]
    if not folders:
        print(f"no client folders in {root}. Create {root.name}/<CLIENT-CODE>/ and "
              f"drop the ITR and 3CD JSON files in.")
        return 0
    for folder in folders:
        files = [f for f in folder.rglob("*") if f.is_file()
                 and not f.name.startswith(".")]
        declared = (ROOT / "clients" / f"{folder.name}.yaml").is_file()
        print(f"{folder.name:34} {len(files):3} file(s)"
              f"{'' if declared else '   (no client context declared)'}")
    return 0


def cmd_rules(args) -> int:
    pack = load_pack(default_pack_path())
    rule_set = load_rules(default_rules_path(), pack, load_fact_map())
    print(f"catalogue v{rule_set.version}  {rule_set.digest}")
    for k, v in rule_set.counts.items():
        print(f"  {k:16} {v}")
    for theme, label in rule_set.themes.items():
        rules = [r for r in rule_set.rules if r.theme == theme]
        print(f"\n{label}  ({len(rules)})")
        for r in rules:
            marker = "·" if r.method == "rule" else "?"
            scope = {"none": "", "year": " [per year]",
                     "year_pair": " [per year pair]"}[r.for_each]
            print(f"  {marker} {r.id:10} {r.question[:88]}{scope}")
    return 0


def cmd_matrix(args) -> int:
    client = ModelClient(refresh(), Budget(max_calls=0))
    result = analyse(args.client, client=client)
    m = result.matrix
    years = m.years
    print("fact".ljust(34) + "".join(y.replace("AY ", "").rjust(16) for y in years))
    for row in m.rows():
        line = row["id"][:33].ljust(34)
        for y in years:
            v = row["values"][y]
            line += ("—" if v is None else
                     money(v) if row["kind"] == "money" else
                     str(v))[:15].rjust(16)
        print(line)
    return 0


def cmd_db(args) -> int:
    """Where the register lives, and whether the schema is up to date."""
    state = store_health()
    print(f"configured  {state.configured}")
    print(f"driver      {state.driver}")
    print(f"connected   {state.connected}")
    print(f"url         {state.as_dict()['url']}")
    if state.server:
        print(f"server      {state.server}")
    if state.reason:
        print(f"reason      {state.reason}")
    if not state.enabled:
        print("\nnothing is being recorded. For one machine:")
        print("  python3 -m tfa.store.local start     # prints a TFA_DB_URL")
        print("then put that line in the env file. For anything shared, point")
        print("TFA_DB_URL at a Postgres of TFA's own — client tax data does not")
        print("belong in another application's schema.")
        return 1
    print(f"applied     {state.migrations_applied} migration(s)")
    if state.migrations_pending:
        print(f"pending     {', '.join(state.migrations_pending)}")
        if args.migrate:
            print("applying:")
            for name in store_migrate():
                pass
            print("done")
        else:
            print("run with --migrate to apply them")
    return 0


def cmd_register(args) -> int:
    """The durable register, as a terminal reads it."""
    book = store_register(args.client)
    if not book.get("available"):
        print(f"the register is unavailable: {book.get('reason')}")
        return 1
    counts = book.get("counts", {})
    print(" · ".join(f"{n} {state}" for state, n in sorted(counts.items())) or "empty")
    print()
    for m in book["matters"]:
        if args.state and m["state"] != args.state:
            continue
        amount = f"  {money(m['amount_inr'])}" if m.get("amount_inr") else ""
        print(f"[{m['state']:11}] {m['rule_id']:10} {(m['ay'] or ''):11}"
              f"{(m['statement'] or '')[:96]}{amount}")
        if m.get("last_action"):
            print(f"{'':13} {m['last_action']} by {m['last_actor']}"
                  f"{' — ' + m['last_note'] if m.get('last_note') else ''}")
    if book.get("enquiries"):
        print(f"\n{len(book['enquiries'])} enquiry(ies) outstanding")
    if book.get("runs"):
        print(f"{len(book['runs'])} run(s) on record; last "
              f"{str(book['runs'][0]['finished_at'])[:16]}")
    return 0


def cmd_run(args) -> int:
    client = (ModelClient(refresh(), Budget(max_calls=0)) if args.no_ai
              else make_client())
    result = analyse(args.client, client=client)
    narrative = synthesise(result, client_name=result.manifest["client"]["name"] or "",
                           years=result.matrix.years,
                           prohibited=result.manifest["doctrine"].get("prohibited", []))

    print(f"\n{result.manifest['client']['name'] or args.client}")
    print(f"{result.filings.attendance.statement}\n")
    counts = result.manifest["verdict_counts"]
    print(f"{counts['FAIL']} matters to address · "
          f"{counts['INSUFFICIENT_EVIDENCE']} undetermined · "
          f"{counts['PASS']} in order · {counts['NOT_APPLICABLE']} not applicable")
    exposure = result.manifest.get("exposure_flagged_inr", 0)
    if exposure:
        print(f"amounts flagged: {money(exposure)}")
    print()
    for f in result.findings:
        if f.verdict != "FAIL":
            continue
        head = f"[{f.severity}] {f.instance_id}"
        print(f"{head:34} {f.statement}")
        if f.enquiry:
            print(f"{'':34} → {f.enquiry}")
        print()

    if getattr(result, "inferences", None):
        print("\n--- inference " + "-" * 58)
        for i in result.inferences:
            mark = {"REPORTABLE": "!", "EXPLAINED": "·", "NOTHING_ARISING": " ",
                    "INSUFFICIENT_EVIDENCE": "?"}.get(i.verdict, " ")
            print(f"{mark} {i.spec_id}  {i.headline or i.reason}")
            for h in i.hypotheses[:2]:
                print(f"     ? {h.get('explanation', '')}")
                if h.get("document"):
                    print(f"       call for: {h['document']}")

    if getattr(result, "programme", None):
        print("\n--- audit programme " + "-" * 51)
        for area in result.programme.areas:
            print(f"[{area.priority:6}] {area.spec.label}"
                  + (f"   {money(area.amount)} in issue" if area.amount else ""))
            for procedure in area.procedures:
                tail = (f"   «{procedure['from_inference']}»"
                        if procedure.get("from_inference") else "")
                print(f"    - {procedure['text']}{tail}")
        for omitted in result.programme.omitted:
            print(f"[  n/a ] {omitted['label']} — {omitted['reason']}")

    ground_truth = ROOT / "generator" / "ground_truth" / f"{args.client}.json"
    score = score_run(result, ground_truth)
    if score:
        print(f"scorecard: {score.detected}/{score.planted} planted defects found "
              f"({score.recall:.0%} recall); {len(score.additional)} further findings "
              f"not in the manifest")
        for missed in score.missed:
            print(f"  MISSED {missed['defect_id']} — {missed['description'][:90]}")

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({
            "manifest": result.manifest,
            "attendance": result.filings.attendance.as_dict(),
            "findings": [f.__dict__ for f in result.findings],
            "report": {
                "scope": narrative.scope, "coverage": narrative.coverage,
                "paragraphs": [p.__dict__ for p in narrative.paragraphs],
                "enquiries": narrative.enquiries,
                "gate": narrative.gate.__dict__,
            },
        }, indent=2, default=str))
        print(f"\nwritten to {out}")
    return 1 if counts["FAIL"] else 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="tfa.cli")
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="client folders on disk")
    sub.add_parser("rules", help="print the canonical rule catalogue")

    run = sub.add_parser("run", help="analyse one client")
    run.add_argument("client")
    run.add_argument("--no-ai", action="store_true",
                     help="deterministic rules only; judgment rules report as not "
                          "assessed")
    run.add_argument("--json", default=None, help="write the full result to a file")

    matrix = sub.add_parser("matrix", help="print the fact matrix")
    matrix.add_argument("client")

    db = sub.add_parser("db", help="the register: where it is and whether it is current")
    db.add_argument("--migrate", action="store_true", help="apply pending migrations")

    reg = sub.add_parser("register", help="the durable register of matters")
    reg.add_argument("client")
    reg.add_argument("--state", default=None,
                     help="open | accepted | rejected | amended | resolved | superseded")

    args = ap.parse_args()
    return {"list": cmd_list, "rules": cmd_rules, "run": cmd_run,
            "matrix": cmd_matrix, "db": cmd_db,
            "register": cmd_register}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
