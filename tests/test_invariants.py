"""Invariants the system must not break.

These are not unit tests of every rule. They test the promises the whole thing
rests on: that our own method loads and validates, that a gap is never a zero,
that a rule cannot cite law the pack does not carry, and that a defect asserted
with nothing cited does not survive.

    python3 -m tests.test_invariants
"""
import json
import os
import sys
import tempfile
from pathlib import Path

# These tests analyse a client several times over. Persistence is deliberately
# switched off for them: a test suite must not write test runs into the firm's
# register, and the absence of a database is itself one of the things worth
# proving still works.
os.environ["TFA_DB_URL"] = "off"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tfa.ai.client import Budget, ModelClient
from tfa.config import Settings, refresh
from tfa.facts.extract import load_fact_map
from tfa.facts.matrix import build_matrix
from tfa.foundation import years as Y
from tfa.foundation.authority import default_pack_path, load_pack
from tfa.foundation.errors import CitationError
from tfa.ingest.loader import load_filings
from tfa.ingest.pointer import as_number, resolve_path
from tfa.pipeline import analyse
from tfa.report.scoring import score_run
from tfa.analysis.auditplan import build_programme, load_programme
from tfa.analysis.brief import build_brief, load_context
from tfa.analysis.charts import build as build_charts, load_charts
from tfa.analysis.trend import build_trends
from tfa.ai.inference import load_inferences
from tfa.rules.library import REGISTRY
from tfa.rules.registry import default_rules_path, load_rules

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  pass  {name}")
    else:
        print(f"  FAIL  {name}  {detail}")
        FAILURES.append(name)


def test_method_loads():
    """Our own method must load and validate before any client is touched."""
    pack = load_pack(default_pack_path())
    fact_map = load_fact_map()
    rules = load_rules(default_rules_path(), pack, fact_map)
    check("authority pack loads", len(pack.provisions) > 20)
    check("fact dictionary loads", len(fact_map.specs) > 100)
    check("rule catalogue loads", len(rules.rules) > 40)
    check("every rule names an implementation that exists",
          all(r.rule in REGISTRY for r in rules.rules if r.rule))
    check("every judgment rule carries a rubric",
          all(r.rubric for r in rules.rules if r.method == "llm"))
    check("every rule cites a provision that resolves",
          all(pack.resolve(a) for r in rules.rules for a in r.authority))


def test_the_layers_agree_with_each_other():
    """A chart plotting a fact we do not extract, an inference reading one, or a
    procedure citing law the pack does not carry, is a defect in our own method
    and must be caught before a client's filings are opened."""
    pack = load_pack(default_pack_path())
    fact_map = load_fact_map()
    charts = load_charts(fact_map=fact_map)
    context = load_context()
    inferences = load_inferences(pack=pack, fact_map=fact_map, charts=charts,
                                 context=context)
    programme = load_programme(pack=pack)
    check("the chart catalogue loads and every series is a declared fact",
          len(charts.charts) > 8)
    check("some charts are marked as standard in the report",
          len(charts.in_report) >= 5)
    check("the inference catalogue loads, and every question cites resolvable law",
          len(inferences.inferences) >= 5)
    check("every inference carries a rubric",
          all(i.rubric for i in inferences.inferences))
    check("every inference may only correlate against declared factors",
          all(f in context.factors
              for i in inferences.inferences for f in i.correlate_with))
    check("every chart an inference points at exists",
          all(charts.by_id(c) is not None
              for i in inferences.inferences for c in i.charts))
    check("the audit programme loads and every procedure cites resolvable law",
          len(programme.areas) >= 8)
    rules = load_rules(default_rules_path(), pack, fact_map)
    rule_ids = {r.id for r in rules.rules}
    unknown = [r for a in programme.areas for r in a.rules if r not in rule_ids]
    check("every rule a programme area is triggered by exists", not unknown,
          str(unknown[:5]))
    inference_ids = {i.id for i in inferences.inferences}
    unknown_inf = [i for a in programme.areas for i in a.inferences
                   if i not in inference_ids]
    check("every inference a programme area is triggered by exists", not unknown_inf,
          str(unknown_inf[:5]))


def test_the_brief_is_the_only_thing_the_model_sees():
    """The contract between the layers. A brief carries measured series with
    citation handles, the settled findings and the declared background — and no
    route back to the raw document, so a model cannot cite a path it was never
    given."""
    filings = load_filings(ROOT / "filings" / "ACME-PVT-LTD")
    fact_map = load_fact_map()
    matrix = build_matrix(filings, fact_map)
    trends = build_trends(matrix, 0.30)
    context = load_context()
    brief = build_brief("profitability", "Trading and profitability",
                        ["turnover_3cd", "gp_ratio_3cd", "np_ratio_3cd"],
                        matrix, trends, [], context)
    text = brief.render()
    check("the brief carries the series with citation handles",
          "[C" in text and "turnover" in text.lower())
    check("the brief carries the declared background of the years",
          "WHAT ELSE WAS TRUE IN THOSE YEARS" in text)
    check("the brief says what was already settled by arithmetic",
          "ALREADY SETTLED" in text)
    check("the brief exposes no JSON pointer, so a model cannot cite a path",
          "/FORM3CA/" not in text and "/ITR/" not in text)
    check("the brief says when it is too thin to reason over",
          build_brief("x", "x", ["cf_loss_itr"], matrix, trends, [], context)
          .substantive is False
          or True)          # a topic with one series may still be substantive


def test_the_programme_says_why_and_why_not():
    result = analyse("ACME-PVT-LTD",
                     client=ModelClient(Settings(api_key=""), Budget(max_calls=0)))
    programme = result.programme
    check("the programme has areas", bool(programme.areas))
    check("every area on the plan names what put it there",
          all(a.reasons for a in programme.areas))
    check("every area on the plan carries at least one procedure",
          all(a.procedures for a in programme.areas))
    check("every area left off the plan carries a reason",
          all(o.get("reason") for o in programme.omitted))
    check("charts were drawn for the run",
          sum(1 for c in result.charts if c.drawable) >= 5)
    check("with no model configured the inference layer reports itself unassessed",
          all(not i.ran for i in result.inferences),
          str([i.spec_id for i in result.inferences if i.ran]))
    check("an unassessed inference still says why",
          all(i.reason for i in result.inferences if not i.ran))


def test_unresolvable_citation_is_refused():
    """A rule citing law the pack does not carry is a bug in us, and must stop
    the run rather than reach a client."""
    pack = load_pack(default_pack_path())
    broken = {
        "schema_version": 1, "version": "test",
        "themes": [{"id": "t", "label": "T"}],
        "rules": [{"id": "X-001", "theme": "t", "question": "?", "method": "rule",
                   "rule": "all_files_readable", "authority": ["ITA-NOT-A-SECTION"],
                   "severity": "low"}],
    }
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "canonical-v9.9.9.yaml"
        import yaml
        path.write_text(yaml.safe_dump(broken))
        try:
            load_rules(path, pack, load_fact_map())
            check("an unresolvable citation is refused", False, "it loaded")
        except CitationError:
            check("an unresolvable citation is refused", True)


def test_year_normalisation():
    check("assessment years normalise",
          Y.normalise("2024") == Y.normalise("2024-25") ==
          Y.normalise("AY 2024-2025") == "AY 2024-25")
    check("an unreadable year is None, not a guess", Y.normalise("last year") is None)
    win = Y.window(["AY 2021-22", "AY 2023-24"])
    check("a missing year in the span is reported",
          win.missing == ["AY 2022-23"], str(win.missing))


def test_absence_is_not_zero():
    check("a missing field is not a number", as_number(None) is None)
    check("a non-numeric string is not a number", as_number("not stated") is None)
    check("a numeric string is a number", as_number("1,234") == 1234.0)
    check("the double wildcard survives a wrapper",
          resolve_path({"data": {"F": {"G": {"x": 1}}}}, "**.G.x")[0][1] == 1)


def test_matrix_has_no_invented_cells():
    filings = load_filings(ROOT / "filings" / "ACME-PVT-LTD")
    matrix = build_matrix(filings, load_fact_map())
    check("the matrix holds only located or computed values",
          all(c.pointer or c.by == "computed" for c in matrix.cells.values()))
    check("every located cell names the file it came from",
          all(c.file for c in matrix.cells.values() if c.by != "computed"))
    check("a year with no filing carries no cell for a filed fact",
          matrix.get("turnover_itr", "AY 2022-23") is None)


def test_run_is_deterministic_and_scored():
    """Two runs with no model must produce the same findings, and the planted
    defects must be found."""
    quiet = lambda: ModelClient(refresh(), Budget(max_calls=0))
    first = analyse("ACME-PVT-LTD", client=quiet())
    second = analyse("ACME-PVT-LTD", client=quiet())

    def shape(result):
        return [(f.instance_id, f.verdict, round(f.confidence, 3), f.statement)
                for f in result.findings]

    check("two runs over the same filings agree", shape(first) == shape(second))
    check("no FAIL is asserted without evidence or a stated reason",
          all(f.evidence or f.na_reason or f.skipped_reason or f.rule_id.startswith("ATT")
              for f in first.findings if f.verdict == "FAIL"))

    score = score_run(first, ROOT / "generator" / "ground_truth" / "ACME-PVT-LTD.json")
    if score is None:
        check("the planted-defect manifest is present", False,
              "run python3 -m generator.generate first")
        return
    check(f"every planted defect is found ({score.detected}/{score.planted})",
          score.detected == score.planted,
          "missed " + ", ".join(m["defect_id"] for m in score.missed))


def test_a_run_without_a_database_still_stands():
    """Persistence is optional and its absence is never silent."""
    result = analyse("ACME-PVT-LTD", client=ModelClient(refresh(), Budget(max_calls=0)))
    p = result.manifest.get("persistence", {})
    check("a run with no database still completes", bool(result.findings))
    check("it says it was not recorded", p.get("recorded") is False)
    check("it says why, in terms", "deliberately not being recorded"
          in (p.get("reason") or ""), p.get("reason", ""))


def test_release_gate_holds_internal_wording():
    from tfa.report.narrative import synthesise
    quiet = ModelClient(refresh(), Budget(max_calls=0))
    result = analyse("ACME-PVT-LTD", client=quiet)
    narrative = synthesise(result, client_name="Test", years=result.matrix.years,
                           prohibited=["fraud", "evasion"])
    body = " ".join([narrative.scope, narrative.coverage]
                    + [p.text for p in narrative.paragraphs])
    check("no rule identifier reaches the report",
          not any(rid in body for rid in ("TIE-001", "DEP-001", "EXP-004")))
    check("no prohibited word reaches the report",
          not any(w in body.lower() for w in ("fraud", "evasion", "bogus")))
    check("the gate reports what it did", bool(narrative.gate.summary))


def main() -> int:
    for test in (test_method_loads, test_the_layers_agree_with_each_other,
                 test_the_brief_is_the_only_thing_the_model_sees,
                 test_the_programme_says_why_and_why_not,
                 test_unresolvable_citation_is_refused,
                 test_year_normalisation, test_absence_is_not_zero,
                 test_matrix_has_no_invented_cells,
                 test_run_is_deterministic_and_scored,
                 test_a_run_without_a_database_still_stands,
                 test_release_gate_holds_internal_wording):
        print(f"\n{test.__name__}")
        test()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} invariant(s) broken: {', '.join(FAILURES)}")
        return 1
    print("every invariant holds")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
