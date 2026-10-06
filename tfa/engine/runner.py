"""The execution engine.

Order of business for every rule instance: applicability, then dependencies,
then evaluation, then the guards. Each step exists because leaving it out
produces a specific kind of wrong answer, and each records what it did, so a
finding can be re-derived months later from the manifest alone.

A rule is instantiated once, once per year, or once per year that has a
predecessor. That is how fifty-eight catalogue entries become several hundred
tested propositions without fifty-eight copies to maintain.
"""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

from ..facts.matrix import Cell, FactMatrix
from ..foundation import years as Y
from ..foundation.hashing import hash_obj
from ..rules.context import (
    FAIL, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE, PASS, RuleContext, RuleResult, VERDICTS,
)
from ..rules.library import REGISTRY
from ..rules.registry import RuleSet, RuleSpec


@dataclass
class Finding:
    instance_id: str
    rule_id: str
    theme: str
    question: str
    verdict: str
    severity: str
    confidence: float
    statement: str                        # internal wording
    observation: str                      # wording safe to put in front of a client
    enquiry: str                          # what to ask, or what to call for
    vulnerability: str                    # what it exposes if left as it is
    ay: Optional[str] = None
    evidence: List[str] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    # The cited cells in structured form: fact, year, value and pointer. The
    # rendered strings above are for reading; these are what the store
    # fingerprints a matter on, and what lets a reader open the file at the
    # exact place the figure came from.
    evidence_cells: List[Dict] = field(default_factory=list)
    facts_used: List[str] = field(default_factory=list)
    authority: List[Dict] = field(default_factory=list)
    amount: Optional[float] = None
    detail: Dict[str, Any] = field(default_factory=dict)
    method: str = "rule"
    provenance_source: str = "rule"
    na_reason: str = ""
    skipped_reason: str = ""
    needs_review: bool = False
    disposition: str = "open"             # open | accepted | rejected | amended
    provenance: List[str] = field(default_factory=list)   # file digests consumed

    @property
    def is_defect(self) -> bool:
        return self.verdict == FAIL


@dataclass
class RunResult:
    findings: List[Finding]
    manifest: Dict
    matrix: FactMatrix
    trends: Dict
    filings: Any
    rule_set: RuleSet
    anomalies: Optional[Any] = None
    persistence: Optional[Dict] = None
    charts: List[Any] = field(default_factory=list)
    inferences: List[Any] = field(default_factory=list)
    programme: Optional[Any] = None
    method: Optional[Any] = None

    def by_verdict(self, verdict: str) -> List[Finding]:
        return [f for f in self.findings if f.verdict == verdict]

    @property
    def defects(self) -> List[Finding]:
        return self.by_verdict(FAIL)

    @property
    def exposure(self) -> float:
        return sum(f.amount or 0 for f in self.defects)


def _topological(rules: Sequence[RuleSpec]) -> List[RuleSpec]:
    by_id = {r.id: r for r in rules}
    ordered: List[RuleSpec] = []
    seen: set = set()

    def visit(r: RuleSpec, stack: tuple) -> None:
        if r.id in seen:
            return
        if r.id in stack:
            raise ValueError(f"dependency cycle through {r.id}")
        for dep in r.requires:
            visit(by_id[dep], stack + (r.id,))
        seen.add(r.id)
        ordered.append(r)

    for r in rules:
        visit(r, ())
    return ordered


def _subjects(spec: RuleSpec, matrix: FactMatrix) -> List[Optional[str]]:
    if spec.for_each == "year":
        return list(matrix.years)
    if spec.for_each == "year_pair":
        return [ay for ay in matrix.years if Y.preceding(ay) in matrix.years]
    return [None]


def run(foundation, matrix: FactMatrix, trends: Dict, filings, rule_set: RuleSet,
        on_finding: Optional[Callable[[Finding], None]] = None,
        client: Optional[Any] = None) -> RunResult:
    threshold = float(foundation.doctrine.param("default_threshold", 0.70))
    absence_safe = filings.attendance.absence_safe

    findings: List[Finding] = []
    index: Dict[str, Finding] = {}

    for spec in _topological(rule_set.rules):
        for ay in _subjects(spec, matrix):
            suffix = f"@{ay}" if ay else ""
            instance_id = f"{spec.id}{suffix}"
            prior = Y.preceding(ay) if ay else None
            authority = [
                {"id": p.id, "citation": p.citation, "title": p.title,
                 "instrument": foundation.pack.instruments.get(p.instrument,
                                                               p.instrument),
                 "verified": p.verified}
                for p in (foundation.pack.resolve(a) for a in spec.authority)
                if p.id in foundation.provisions_in_force
            ]

            def make(result: RuleResult, **over) -> Finding:
                f = Finding(
                    instance_id=instance_id, rule_id=spec.id, theme=spec.theme,
                    question=spec.question, verdict=result.verdict,
                    severity=spec.severity, confidence=result.confidence,
                    statement=result.statement, observation=result.observation,
                    enquiry=result.enquiry or spec.enquiry,
                    vulnerability=spec.vulnerability,
                    ay=ay, amount=result.amount,
                    evidence=[c.render() for c in result.evidence],
                    evidence_refs=[c.ref for c in result.evidence],
                    evidence_cells=[{"fact": c.fact_id, "ay": c.ay,
                                     "value": c.value, "pointer": c.pointer,
                                     "file": c.file, "by": c.by, "ref": c.ref}
                                    for c in result.evidence],
                    facts_used=sorted({c.fact_id for c in result.evidence}),
                    authority=authority, detail=result.detail,
                    na_reason=result.na_reason, method=spec.method,
                    provenance=sorted({c.file for c in result.evidence if c.file}),
                )
                for k, v in over.items():
                    setattr(f, k, v)
                return f

            # ---- applicability -------------------------------------------
            blocked = [p for p in spec.applicability
                       if not foundation.predicates.get(p, False)]
            if blocked:
                finding = make(RuleResult(
                    NOT_APPLICABLE,
                    f"Applicability predicate(s) {blocked} are false for this client.",
                    confidence=1.0,
                    na_reason=f"predicate(s) {', '.join(blocked)} do not hold for this "
                              f"assessee"))
                findings.append(finding)
                index[instance_id] = finding
                if on_finding:
                    on_finding(finding)
                continue

            # ---- dependencies --------------------------------------------
            unmet = []
            for dep in spec.requires:
                dep_instance = index.get(f"{dep}{suffix}") or index.get(dep)
                if dep_instance is None or dep_instance.verdict in (
                        FAIL, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE):
                    unmet.append(dep_instance.instance_id if dep_instance else dep)
            if unmet:
                finding = make(
                    RuleResult(INSUFFICIENT_EVIDENCE,
                               f"Not evaluated: {', '.join(unmet)} was not satisfied.",
                               confidence=1.0),
                    skipped_reason=f"depends on {', '.join(unmet)}")
                findings.append(finding)
                index[instance_id] = finding
                if on_finding:
                    on_finding(finding)
                continue

            # ---- evaluate --------------------------------------------------
            ctx = RuleContext(foundation=foundation, matrix=matrix, trends=trends,
                              filings=filings, ay=ay,
                              prior_ay=(prior if prior in matrix.years else None),
                              params=dict(spec.params))
            result = _evaluate(ctx, spec, client)
            if result.verdict not in VERDICTS:
                raise ValueError(f"{spec.id} returned invalid verdict "
                                 f"{result.verdict!r}")

            # ---- absence guard ---------------------------------------------
            # A defect asserted with nothing cited, on a folder that was not read
            # completely, is downgraded: we cannot tell absence from unread.
            if (result.verdict == FAIL and not result.evidence
                    and not absence_safe):
                result = RuleResult(
                    INSUFFICIENT_EVIDENCE,
                    result.statement + " Downgraded: a file that could have been a "
                                       "filing was not read, so an absence cannot be "
                                       "asserted.",
                    confidence=min(result.confidence, 0.6),
                    observation=result.observation, enquiry=result.enquiry)

            # ---- confidence gate -------------------------------------------
            needs_review = (result.verdict in (FAIL, INSUFFICIENT_EVIDENCE)
                            and result.confidence < threshold)
            ai_meta = (result.detail or {}).get("ai", {})
            if spec.method == "rule":
                source = "rule"
            elif ai_meta.get("cached"):
                source = "model (reproduced from cache)"
            elif ai_meta.get("ok"):
                source = "model (fresh call)"
            else:
                source = "not assessed"

            finding = make(result, needs_review=needs_review,
                           provenance_source=source)
            if needs_review and finding.verdict == FAIL:
                finding.verdict = INSUFFICIENT_EVIDENCE
                finding.statement += (
                    f" Confidence {result.confidence:.2f} is below the "
                    f"{threshold:.2f} threshold, so this is routed to review rather "
                    f"than reported as a matter to address.")

            findings.append(finding)
            index[instance_id] = finding
            if on_finding:
                on_finding(finding)

    manifest = dict(foundation.manifest())
    manifest.update({
        "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rules": {"version": rule_set.version, "digest": rule_set.digest,
                  "declared": len(rule_set.rules),
                  "instances_evaluated": len(findings)},
        "fact_map": {"version": matrix.fact_map.version,
                     "digest": matrix.fact_map.digest,
                     **matrix.coverage()},
        "attendance": filings.attendance.as_dict(),
        "filing_digests": {f.name: f.digest for f in filings.filings},
        "verdict_counts": {v: sum(1 for f in findings if f.verdict == v)
                           for v in VERDICTS},
        "method_counts": {m: sum(1 for f in findings if f.method == m)
                          for m in ("rule", "llm", "hybrid")},
        "exposure_flagged_inr": sum(f.amount or 0 for f in findings
                                    if f.verdict == FAIL),
    })
    if client is not None:
        manifest["ai"] = dict(client.settings.manifest())
        manifest["ai"].update(client.budget.manifest())
        manifest["method"] = (
            "deterministic rules over the fact matrix, with model judgment on the "
            f"questions arithmetic cannot answer (model {client.settings.model})"
            if client.available else
            "deterministic rules over the fact matrix; model judgment was not "
            "available for this run and the affected rules are reported as not "
            "assessed")
    else:
        manifest["ai"] = {"ai_available": False}
        manifest["method"] = "deterministic rules over the fact matrix only"
    manifest["manifest_digest"] = hash_obj(manifest)

    return RunResult(findings=findings, manifest=manifest, matrix=matrix,
                     trends=trends, filings=filings, rule_set=rule_set)


def _evaluate(ctx: RuleContext, spec: RuleSpec, client) -> RuleResult:
    """Dispatch by method. A judgment rule with no model available is reported
    as not assessed — never answered by a rule that was not written for it."""
    from ..ai.judge import arbitrate, judge

    if spec.method == "rule":
        return REGISTRY[spec.rule](ctx)

    model_ready = client is not None and getattr(client, "available", False)

    if spec.method == "llm":
        if not model_ready:
            reason = (client.settings.unavailable_reason() if client
                      else "no model client was configured for this run")
            return RuleResult(
                INSUFFICIENT_EVIDENCE,
                f"Not assessed by model judgment: {reason}.",
                confidence=0.3, observation="",
                detail={"ai": {"ok": False, "reason": reason, "skipped": True}})
        return judge(ctx, spec, client)

    rule_result = REGISTRY[spec.rule](ctx)
    if not model_ready:
        rule_result.detail = dict(rule_result.detail or {})
        rule_result.detail["arbitration"] = {
            "outcome": "rule only",
            "reason": (client.settings.unavailable_reason() if client
                       else "no model client was configured for this run")}
        return rule_result
    return arbitrate(rule_result, judge(ctx, spec, client))
