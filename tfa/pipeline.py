"""End to end: preflight, read the folder, build the matrix, measure the trends,
run the rules, compare the years openly, draft the report.

`analyse` runs it and hands back the result. `analyse_streaming` runs the same
pipeline and yields an event per real step, so a caller can show progress that
reflects the work rather than a guess at it.
"""
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .ai.anomaly import AnomalyScan, scan as scan_anomalies
from .ai.client import Budget, ModelClient
from .ai.inference import (
    Inference, default_inference_path, infer_all, load_inferences,
)
from .analysis.auditplan import build_programme, load_programme
from .analysis.brief import load_context
from .analysis.charts import build as build_charts, load_charts
from .analysis.trend import build_trends, summarise as summarise_trends
from .config import ROOT, Settings, refresh
from .engine.runner import Finding, RunResult, run
from .facts.extract import load_fact_map
from .facts.matrix import build_matrix
from .foundation.hashing import hash_obj
from .foundation.preflight import Foundation, preflight
from .ingest.loader import load_filings
from .report.narrative import Narrative, synthesise
from .report.scoring import score_run
from .rules.context import RuleContext
from .rules.registry import default_rules_path, load_rules
from .store import health as store_health, record_run


def make_client(settings: Optional[Settings] = None) -> ModelClient:
    """Re-reads the environment each time, so putting a key in `env` does not
    require a restart."""
    s = settings or refresh()
    return ModelClient(s, Budget(max_calls=s.max_model_calls))


def anomalies_as_findings(scan: AnomalyScan) -> List[Finding]:
    """Matters found by open comparison enter the same register as everything
    else, so they inherit the citation rule, the disposition field and the
    release gate. They carry no canonical question, because none was asked."""
    out: List[Finding] = []
    for i, item in enumerate(scan.found, start=1):
        out.append(Finding(
            instance_id=f"ANM-{i:03d}", rule_id="ANM", theme="open",
            question="(no canonical question — found by open comparison of the years)",
            verdict="FAIL", severity=item.severity, confidence=item.confidence,
            statement=f"[{item.kind}] {item.statement}",
            observation=item.observation, enquiry=item.enquiry,
            vulnerability="Not tested by any rule in the catalogue. If it holds, it is "
                          "a candidate for a new canonical rule.",
            ay=(item.evidence[0].ay if item.evidence else None),
            evidence=[c.render() for c in item.evidence],
            evidence_refs=[c.ref for c in item.evidence],
            evidence_cells=[{"fact": c.fact_id, "ay": c.ay, "value": c.value,
                             "pointer": c.pointer, "file": c.file, "by": c.by,
                             "ref": c.ref} for c in item.evidence],
            facts_used=sorted({c.fact_id for c in item.evidence}),
            authority=[], method="llm",
            provenance_source=("model (reproduced from cache)" if scan.cached
                               else "model (fresh call)"),
            detail={"kind": item.kind, "open_ended": True,
                    "dropped_citations": item.dropped, "model": scan.model},
        ))
    return out


@dataclass
class Method:
    """Everything the firm maintains, loaded and cross-checked before a filing
    is opened. A chart plotting a fact we do not extract, an inference reading
    one, a procedure citing law the pack does not carry — each is a defect in
    our own method, and each stops the run here rather than reaching a client."""
    foundation: Any
    fact_map: Any
    rules: Any
    charts: Any
    context: Any
    inferences: Any
    programme: Any

    def manifest(self) -> Dict:
        return {
            "charts": {"version": self.charts.version, "digest": self.charts.digest,
                       "declared": len(self.charts.charts),
                       "in_report": len(self.charts.in_report)},
            "context": self.context.manifest(),
            "inference": {"version": self.inferences.version,
                          "digest": self.inferences.digest,
                          **self.inferences.counts},
            "programme": {"version": self.programme.version,
                          "digest": self.programme.digest,
                          "areas": len(self.programme.areas),
                          "procedures": sum(len(a.procedures)
                                            for a in self.programme.areas)},
        }


def load_method(client_id: str, filings_root: Optional[Path] = None) -> Method:
    foundation = preflight(client_id, filings_root=filings_root)
    fact_map = load_fact_map()
    rules = load_rules(default_rules_path(), foundation.pack, fact_map)
    charts = load_charts(fact_map=fact_map)
    context = load_context()
    inferences = load_inferences(pack=foundation.pack, fact_map=fact_map,
                                 charts=charts, context=context)
    programme = load_programme(pack=foundation.pack)
    return Method(foundation=foundation, fact_map=fact_map, rules=rules,
                  charts=charts, context=context, inferences=inferences,
                  programme=programme)


def inferences_as_findings(results: List[Inference]) -> List[Finding]:
    """A reportable inference joins the same register as everything else.

    It inherits the citation rule, the disposition field and the release gate,
    and it is marked as model judgment so nobody mistakes it for arithmetic.
    What it does not inherit is a canonical question, because the question it
    answers is one of inference rather than of measurement.
    """
    out: List[Finding] = []
    for i in results:
        if not i.reportable:
            continue
        detail = []
        for a in i.aberrations:
            detail.append(f"{a.get('ay', '')}: {a.get('what', '')} "
                          f"({a.get('corroborated', 'neither')}"
                          + (f" — {a.get('corroboration_detail')}"
                             if a.get("corroboration_detail") else "") + ")")
        for c in i.correlations:
            detail.append(f"correlated with {c.get('factor_label', c.get('factor'))}"
                          f"{' in ' + c['ay'] if c.get('ay') else ''}: "
                          f"{c.get('effect', '')}")
        enquiry = " ".join(
            f"{h.get('explanation', '')} — confirmed by {h.get('confirms_if', '')}."
            + (f" Call for: {h['document']}." if h.get("document") else "")
            for h in i.hypotheses[:2]).strip()
        out.append(Finding(
            instance_id=i.spec_id, rule_id=i.spec_id, theme="inference",
            question=i.question, verdict="FAIL", severity=i.severity,
            confidence=i.confidence,
            statement=" ".join([i.headline] + detail).strip(),
            observation=i.observation, enquiry=enquiry,
            vulnerability=i.vulnerability,
            ay=(i.evidence[0].ay if i.evidence else None),
            evidence=[c.render() for c in i.evidence],
            evidence_refs=[c.ref for c in i.evidence],
            evidence_cells=[{"fact": c.fact_id, "ay": c.ay, "value": c.value,
                             "pointer": c.pointer, "file": c.file, "by": c.by,
                             "ref": c.ref} for c in i.evidence],
            facts_used=sorted({c.fact_id for c in i.evidence}),
            authority=i.authority, method="llm",
            provenance_source=("model (reproduced from cache)" if i.cached
                               else "model (fresh call)"),
            detail={"inference": True, "topic": i.topic, "charts": i.charts,
                    "hypotheses": i.hypotheses, "audit_focus": i.audit_focus,
                    "notes": i.notes, "years_reasoned_over": i.years_reasoned_over},
        ))
    return out


def _load(client_id: str, client: ModelClient, filings_root: Optional[Path] = None):
    method = load_method(client_id, filings_root)
    return method.foundation, method.fact_map, method.rules


def _observed(matrix) -> Dict[str, bool]:
    """The predicates that can only be known once the filings have been read."""
    presumptive = any(matrix.text("presumptive_section_3cd", ay)
                      for ay in matrix.years)
    audit = any(matrix.has_source(ay, "3cd") for ay in matrix.years)
    # A company reporting no book profit in any year is the trace a concessional
    # regime leaves in these documents.
    company_years = [ay for ay in matrix.years if matrix.has_source(ay, "itr")]
    concessional = bool(company_years) and not any(
        matrix.num("book_profit_115jb_itr", ay) for ay in company_years)
    return {"presumptive_claimed": presumptive, "audit_report_present": audit,
            "concessional_regime": concessional}


def analyse(client_id: str, filings_root: Optional[Path] = None,
            client: Optional[ModelClient] = None) -> RunResult:
    client = client or make_client()
    method = load_method(client_id, filings_root)
    foundation = method.foundation
    filings = load_filings(foundation.filings_root)
    matrix = build_matrix(filings, method.fact_map)
    foundation.settle(matrix.years, _observed(matrix))
    material = float(foundation.doctrine.param("yoy_material_change", 0.30))
    trends = build_trends(matrix, material)

    # ---- the deterministic half ------------------------------------------
    result = run(foundation, matrix, trends, filings, method.rules, client=client)
    result.charts = build_charts(matrix, method.charts)

    ctx = RuleContext(foundation=foundation, matrix=matrix, trends=trends,
                      filings=filings)
    result.anomalies = scan_anomalies(ctx, client, existing=result.findings)
    result.findings.extend(anomalies_as_findings(result.anomalies))

    # ---- the inference half, over briefs the program wrote ---------------
    result.inferences = infer_all(method.inferences, matrix, trends, result.findings,
                                  foundation, client, method.context)
    result.findings.extend(inferences_as_findings(result.inferences))

    # ---- and deterministic again, to assemble the programme --------------
    result.programme = build_programme(result.findings, result.inferences, trends,
                                       method.programme, material)
    result.method = method
    _restate(result, client, method)
    result.persistence = persist(result, foundation, filings, matrix)
    return result


def persist(result, foundation, filings, matrix) -> Dict:
    """Write the run to the register, if there is a register to write to.

    A database that is absent is not an error: the analysis is a function of
    the filings and stands on its own. A database that is present and refuses
    the write *is* worth shouting about, because the decisions people record
    are the one thing a re-run cannot reconstruct — so the reason is put in the
    manifest and on the screen rather than into a log nobody reads.
    """
    outcome = record_run(result, foundation, filings, matrix)
    result.manifest["persistence"] = {
        "recorded": outcome.get("recorded", False),
        "run_id": outcome.get("run_id"),
        "reason": outcome.get("reason", ""),
        "register": outcome.get("reconciliation"),
    }
    return outcome


def _restate(result: RunResult, client: ModelClient, method=None) -> None:
    """The open comparison runs after the rules, so the counts have to be
    recomputed rather than left describing an earlier moment."""
    from .rules.context import VERDICTS
    m = result.manifest
    m["verdict_counts"] = {v: sum(1 for f in result.findings if f.verdict == v)
                           for v in VERDICTS}
    m["rules"]["instances_evaluated"] = len(result.findings)
    m["exposure_flagged_inr"] = sum(f.amount or 0 for f in result.findings
                                    if f.verdict == "FAIL")
    s = result.anomalies
    m["open_comparison"] = {
        "ran": bool(s and s.ran), "reason": (s.reason if s else "not attempted"),
        "found": len(s.found) if s else 0,
        "suppressed": len(s.suppressed) if s else 0,
        "cells_compared": s.cells_compared if s else 0,
        "coverage_note": (s.coverage_note if s else ""),
    }
    if method is not None:
        m["method"] = m.get("method") if isinstance(m.get("method"), str) else None
        m.update(method.manifest())
    inferences = result.inferences or []
    m["inference"] = dict(m.get("inference", {}) or {})
    m["inference"].update({
        "run": len(inferences),
        "reportable": sum(1 for i in inferences if i.reportable),
        "explained": sum(1 for i in inferences if i.verdict == "EXPLAINED"),
        "nothing_arising": sum(1 for i in inferences
                               if i.verdict == "NOTHING_ARISING"),
        "not_assessed": sum(1 for i in inferences if not i.ran),
    })
    if result.programme is not None:
        m["programme"] = dict(m.get("programme", {}) or {})
        m["programme"].update(result.programme.counts)
    if result.charts:
        m["charts"] = dict(m.get("charts", {}) or {})
        m["charts"].update({"drawable": sum(1 for c in result.charts if c.drawable)})
    if client is not None:
        m["ai"] = dict(client.settings.manifest())
        m["ai"].update(client.budget.manifest())
    m["manifest_digest"] = hash_obj({k: v for k, v in m.items()
                                     if k != "manifest_digest"})


def _plain(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def _finding_event(f: Finding, theme_label: str) -> Dict:
    return {
        "instance_id": f.instance_id, "rule_id": f.rule_id, "theme": f.theme,
        "theme_label": theme_label, "question": f.question, "verdict": f.verdict,
        "severity": f.severity, "confidence": f.confidence, "statement": f.statement,
        "observation": f.observation, "enquiry": f.enquiry,
        "vulnerability": f.vulnerability, "ay": f.ay, "amount": f.amount,
        "evidence": f.evidence, "facts_used": f.facts_used,
        "authority": [a["citation"] for a in f.authority],
        "na_reason": f.na_reason, "skipped_reason": f.skipped_reason,
        "needs_review": f.needs_review, "method": f.method,
        "provenance_source": f.provenance_source,
    }


def analyse_streaming(client_id: str, filings_root: Optional[Path] = None,
                      client: Optional[ModelClient] = None) -> Iterator[Dict]:
    """Yields: start, preflight, rules, reading, read, attendance, matrix,
    trends, assessing, assess, comparing, anomaly, compared, report, done."""
    t0 = time.perf_counter()
    client = client or make_client()

    def ms() -> float:
        return round((time.perf_counter() - t0) * 1000, 1)

    yield {"stage": "start", "at": datetime.now().isoformat(timespec="seconds")}

    # ---- preflight ---------------------------------------------------------
    method = load_method(client_id, filings_root)
    foundation, fact_map, rule_set = method.foundation, method.fact_map, method.rules
    c = foundation.client
    yield {"stage": "preflight", "ms": ms(), "detail": {
        "client_id": c.client_id, "name": c.display_name, "pan": c.pan,
        "entity_type": c.entity_type, "declared": c.declared, "source": c.source,
        "business": c.nature_of_business,
        "run_mode": c.run_mode,
        "folder": str(foundation.filings_root),
        "pack": f"{foundation.pack.id} v{foundation.pack.version}",
        "pack_status": foundation.pack.verification_status,
        "provisions": len(foundation.pack.provisions),
        "doctrine": foundation.doctrine.version,
        "doctrine_articles": len(foundation.doctrine.articles),
        "ai_available": client.available,
        "ai_reason": client.settings.unavailable_reason(),
        "model": client.settings.model if client.available else None,
        "warnings": foundation.warnings,
    }}

    yield {"stage": "rules", "ms": ms(), "detail": {
        "version": rule_set.version, "digest": rule_set.digest,
        **rule_set.counts,
        "themes": [{"id": t, "label": label} for t, label in rule_set.themes.items()],
        "fact_map_version": fact_map.version,
        "facts_declared": len(fact_map.specs),
        "charts_declared": len(method.charts.charts),
        "inferences_declared": len(method.inferences.inferences),
        "programme_areas": len(method.programme.areas),
        "context_periods": len(method.context.periods),
    }}

    # ---- read the folder ---------------------------------------------------
    root = foundation.filings_root
    total = sum(1 for p in root.rglob("*")
                if p.is_file() and not p.name.startswith(("~$", ".")))
    yield {"stage": "reading", "ms": ms(),
           "detail": {"folder": str(root), "files_found": total}}

    events: List[Dict] = []
    filings = load_filings(root, on_file=events.append)
    for i, ev in enumerate(events, start=1):
        yield {"stage": "read", "index": i, "of": total, **ev}

    yield {"stage": "attendance", "ms": ms(), "detail": filings.attendance.as_dict()}

    # ---- the fact matrix ---------------------------------------------------
    matrix = build_matrix(filings, fact_map)
    foundation.settle(matrix.years, _observed(matrix))
    yield {"stage": "matrix", "ms": ms(), "detail": {
        "years": matrix.years,
        "coverage": matrix.coverage(),
        "groups": [{"id": g, "label": label}
                   for g, label in fact_map.groups.items()],
        "rows": matrix.rows(),
        "notes": matrix.notes,
        "sources": {f"{ay}|{kind}": name
                    for (ay, kind), name in matrix.sources.items()},
    }}

    # ---- trends ------------------------------------------------------------
    material = float(foundation.doctrine.param("yoy_material_change", 0.30))
    trends = build_trends(matrix, material)
    yield {"stage": "trends", "ms": ms(), "detail": {
        "summary": summarise_trends(trends, material),
        "series": [t.as_dict(material) for t in trends.values()],
    }}

    # ---- charts ------------------------------------------------------------
    charts = build_charts(matrix, method.charts)
    yield {"stage": "charts", "ms": ms(), "detail": {
        "version": method.charts.version,
        "groups": [{"id": g, "label": label}
                   for g, label in method.charts.groups.items()],
        "charts": [ch.as_dict() for ch in charts],
        "drawable": sum(1 for ch in charts if ch.drawable),
        "in_report": [ch.spec.id for ch in charts if ch.spec.report and ch.drawable],
    }}

    # ---- rules -------------------------------------------------------------
    yield {"stage": "assessing", "ms": ms(), "detail": {
        "rules": len(rule_set.rules), "years": len(matrix.years)}}
    emitted: List[Finding] = []
    result = run(foundation, matrix, trends, filings, rule_set,
                 on_finding=emitted.append, client=client)
    for i, f in enumerate(emitted, start=1):
        yield {"stage": "assess", "index": i, "of": len(emitted),
               **_finding_event(f, rule_set.theme_label(f.theme))}

    # ---- open comparison ---------------------------------------------------
    yield {"stage": "comparing", "ms": ms(), "detail": {
        "years": len(matrix.years), "cells": len(matrix.cells),
        "ai_available": client.available,
        "reason": client.settings.unavailable_reason()}}
    ctx = RuleContext(foundation=foundation, matrix=matrix, trends=trends,
                      filings=filings)
    result.anomalies = scan_anomalies(ctx, client, existing=result.findings)
    extra = anomalies_as_findings(result.anomalies)
    result.findings.extend(extra)
    for i, f in enumerate(extra, start=1):
        yield {"stage": "anomaly", "index": i, "of": len(extra),
               "kind": f.detail.get("kind", ""),
               **_finding_event(f, "Found by open comparison")}
    yield {"stage": "compared", "ms": ms(), "detail": {
        "ran": result.anomalies.ran, "reason": result.anomalies.reason,
        "found": len(result.anomalies.found),
        "suppressed": result.anomalies.suppressed,
        "cells_compared": result.anomalies.cells_compared,
        "coverage_note": result.anomalies.coverage_note,
    }}
    result.charts = charts

    # ---- inference, over briefs this program wrote ------------------------
    yield {"stage": "inferring", "ms": ms(), "detail": {
        "declared": len(method.inferences.inferences),
        "topics": [{"id": t, "label": label}
                   for t, label in method.inferences.topics.items()],
        "context_version": method.context.version,
        "context_status": method.context.status,
        "ai_available": client.available,
        "reason": client.settings.unavailable_reason(),
    }}
    inferred: List[Inference] = []
    for spec in method.inferences.inferences:
        from .analysis.brief import build_brief
        brief = build_brief(topic=spec.topic, title=spec.title, facts=spec.facts,
                            matrix=matrix, trends=trends, findings=result.findings,
                            context=method.context, charts=spec.charts,
                            themes=spec.settled_themes)
        from .ai.inference import run_inference
        outcome = run_inference(spec, brief, foundation, client, method.context)
        inferred.append(outcome)
        yield {"stage": "inference", "index": len(inferred),
               "of": len(method.inferences.inferences),
               "detail": outcome.as_dict(include_brief=True)}
    result.inferences = inferred
    extra_inference = inferences_as_findings(inferred)
    result.findings.extend(extra_inference)
    yield {"stage": "inferred", "ms": ms(), "detail": {
        "run": len(inferred),
        "reportable": sum(1 for i in inferred if i.reportable),
        "explained": sum(1 for i in inferred if i.verdict == "EXPLAINED"),
        "nothing_arising": sum(1 for i in inferred
                               if i.verdict == "NOTHING_ARISING"),
        "not_assessed": sum(1 for i in inferred if not i.ran),
    }}

    # ---- the audit programme, assembled deterministically ------------------
    result.programme = build_programme(result.findings, inferred, trends,
                                       method.programme, material)
    result.method = method
    yield {"stage": "programme", "ms": ms(),
           "detail": result.programme.as_dict()}

    _restate(result, client, method)

    # ---- report ------------------------------------------------------------
    narrative = synthesise(result, client_name=c.display_name, years=matrix.years,
                           prohibited=foundation.doctrine.prohibited)
    ground_truth = ROOT / "generator" / "ground_truth" / f"{client_id}.json"
    score = score_run(result, ground_truth)

    yield {"stage": "report", "ms": ms(), "detail": {
        "client": c.display_name, "pan": c.pan,
        "years": matrix.years,
        "scope": narrative.scope, "coverage": narrative.coverage,
        "paragraphs": [{
            "theme": p.theme, "heading": p.heading, "text": p.text,
            "years": p.years, "references": p.references, "authority": p.authority,
            "sources": p.sources, "amount": p.amount,
        } for p in narrative.paragraphs],
        "enquiries": narrative.enquiries,
        "exposure": narrative.exposure,
        # The charts are part of the report, in the order the catalogue
        # declares, with the note that says what to take from each.
        "charts": [ch.as_dict() for ch in charts if ch.spec.report and ch.drawable],
        "inference": [i.as_dict() for i in inferred],
        "programme": result.programme.as_dict(),
        "vulnerabilities": [
            {"ref": f.instance_id, "ay": f.ay, "severity": f.severity,
             "amount": f.amount, "text": f.vulnerability,
             "matter": f.observation or f.statement[:200]}
            for f in result.findings
            if f.verdict == "FAIL" and (f.vulnerability or "").strip()],
        "gate": {"passed": narrative.gate.passed, "summary": narrative.gate.summary,
                 "leaks": narrative.gate.leaks,
                 "prohibited": narrative.gate.prohibited,
                 "unobserved": narrative.gate.unobserved},
        "score": (None if not score else {
            "planted": score.planted, "detected": score.detected,
            "recall": round(score.recall, 4), "matched": score.matched,
            "missed": score.missed, "additional": score.additional}),
        "manifest": result.manifest,
        "verdict_counts": result.manifest["verdict_counts"],
    }}

    # ---- the register -----------------------------------------------------
    outcome = persist(result, foundation, filings, matrix)
    yield {"stage": "recorded", "ms": ms(), "detail": {
        "recorded": outcome.get("recorded", False),
        "run_id": outcome.get("run_id"),
        "reason": outcome.get("reason", ""),
        "register": outcome.get("reconciliation"),
        "store": store_health().as_dict(),
    }}

    ai = result.manifest.get("ai", {})
    spend = ai.get("cost", {})
    yield {"stage": "done", "ms": ms(), "detail": {
        "findings": len(result.findings),
        "defects": sum(1 for f in result.findings if f.verdict == "FAIL"),
        "exposure": result.manifest.get("exposure_flagged_inr", 0),
        "ai_available": bool(ai.get("ai_available")),
        "ai_usd": float(spend.get("total_usd", 0.0)),
        "billed_calls": spend.get("billed_calls", 0),
        "cached_calls": spend.get("cached_calls", 0),
        "by_rule": spend.get("by_rule", []),
        "inferences_reportable": sum(1 for i in inferred if i.reportable),
        "programme_areas": result.programme.counts["areas"],
    }}
