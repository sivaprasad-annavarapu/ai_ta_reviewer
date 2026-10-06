"""Model judgment on the questions arithmetic cannot answer.

Whether a fall in margin is corroborated by the other series; whether a head
has stopped being reported rather than stopped occurring; what a reader of
these filings would ask first. Not sums, not continuity, not tie-outs — those
are decided by rule, and a model asked to redo them is slower, dearer and
occasionally wrong.

Everything the model is told about the law comes from the pinned authority
pack. Its own recollection of a section is not authority, so it is never asked
to supply one.
"""
from typing import Dict, List, Optional, Sequence

from ..rules.context import (
    FAIL, INSUFFICIENT_EVIDENCE, NOT_APPLICABLE, PASS, RuleContext, RuleResult,
)
from ..rules.registry import RuleSpec
from .client import ModelClient
from .prompts import (
    CITATION_PROPERTY, DIRECTIVE_PROPERTY, Material, SYSTEM_RULES, VERDICT_ENUM,
    render_findings, render_material, resolve_citations, select,
)

JUDGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": VERDICT_ENUM,
                    "description": "FAIL only where the cells positively establish the "
                                   "matter. INSUFFICIENT_EVIDENCE where the material is "
                                   "silent, partial or ambiguous."},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1,
                       "description": "How firmly the cited cells support the verdict."},
        "statement": {"type": "string",
                      "description": "Two or three sentences for the firm's internal "
                                     "record. Name the years and the series involved."},
        "observation": {"type": "string",
                        "description": "The same point written for the client: plain, "
                                       "factual, no rule names, no characterisation of "
                                       "any person. Empty if the verdict is PASS or "
                                       "NOT_APPLICABLE."},
        "enquiry": {"type": "string",
                    "description": "What to ask the client, or which document to call "
                                   "for, to settle this. One or two sentences, "
                                   "specific enough to act on."},
        "years_reasoned_over": {"type": "integer",
                                "description": "How many assessment years carried the "
                                               "figures you relied on."},
        "citations": CITATION_PROPERTY,
        "directive_text_found": DIRECTIVE_PROPERTY,
        "reasoning": {"type": "string",
                      "description": "Why the cells lead to this verdict, in at most "
                                     "four sentences. Internal record."},
    },
    "required": ["verdict", "confidence", "statement", "enquiry", "citations",
                 "directive_text_found"],
}


def _authority_brief(ctx: RuleContext, spec: RuleSpec) -> str:
    lines = []
    for provision_id in spec.authority:
        p = ctx.foundation.pack.resolve(provision_id)
        if p.id not in ctx.foundation.provisions_in_force:
            continue
        instrument = ctx.foundation.pack.instruments.get(p.instrument, p.instrument)
        lines.append(f"- {p.reference} ({instrument}) — {p.title}. {p.summary}")
    if not lines:
        return "(no provision in the pinned pack bears on this question for these years)"
    return "\n".join(lines)


def build_material(ctx: RuleContext, spec: RuleSpec,
                   findings: Optional[Sequence] = None) -> Material:
    scope = spec.scope or {}
    selection = select(ctx.matrix,
                       groups=scope.get("groups"),
                       facts=scope.get("facts"),
                       years=(ctx.matrix.years if scope.get("years", "all") == "all"
                              else [ctx.ay] if ctx.ay else ctx.matrix.years))
    extra = ""
    if scope.get("findings") and findings is not None:
        extra = render_findings(findings)
    return render_material(ctx.matrix, selection,
                           trends=(ctx.trends if scope.get("trends") else None),
                           extra=extra)


def judge(ctx: RuleContext, spec: RuleSpec, client: ModelClient,
          findings: Optional[Sequence] = None) -> RuleResult:
    material = build_material(ctx, spec, findings)
    if not material.cells:
        return RuleResult(
            INSUFFICIENT_EVIDENCE,
            "No fact within the scope of this question was extracted from the "
            "filings, so it was not put to model judgment.",
            confidence=0.5, observation="",
            enquiry="Check the fact dictionary against these filings: a scope that "
                    "matches nothing usually means the fields moved in the schema.")

    client_ctx = ctx.foundation.client
    prompt = f"""\
QUESTION TO ANSWER
{spec.question}

THE REQUIREMENT, AS IT STANDS IN THE APPLICABLE LAW
{_authority_brief(ctx, spec)}

HOW TO DECIDE
{spec.rubric or 'Answer the question on the cells provided.'}

THE ASSESSEE, AS DECLARED BY THE FIRM
- Name: {client_ctx.display_name}
- Constitution: {client_ctx.entity_type or 'not declared'}
- Business: {client_ctx.nature_of_business or 'not declared'}
- Assessment years on file: {', '.join(ctx.matrix.years) or 'none'}
{'- NOTE: nothing about the assessee was declared; the description above is what the filings say about themselves.' if not client_ctx.declared else ''}

{material.text}

AVAILABLE CELL HANDLES: {material.handles}

Answer the question. Cite only handles from that list.
"""

    reply = client.structured(
        system=SYSTEM_RULES, user=prompt, schema=JUDGMENT_SCHEMA,
        tool_name="record_judgment",
        tool_description="Record the verdict on the question, with citations.",
        label=f"{spec.id}{'@' + ctx.ay if ctx.ay else ''}",
        model=(spec.scope or {}).get("model") or None,
        effort=(spec.scope or {}).get("effort") or None,
        max_tokens=(spec.scope or {}).get("max_tokens") or None,
    )

    if reply.failed:
        return RuleResult(
            INSUFFICIENT_EVIDENCE, f"Not assessed: {reply.reason}.",
            confidence=0.4, observation="",
            detail={"ai": {"ok": False, "reason": reply.reason,
                           "cells_offered": material.count}})

    return _validate(ctx, spec, reply.data or {}, material, reply)


def _validate(ctx: RuleContext, spec: RuleSpec, data: Dict, material: Material,
              reply) -> RuleResult:
    """Nothing the model returns is taken on trust: the enum, the citations, the
    language policy and the rule that a FAIL needs evidence are all re-checked."""
    notes: List[str] = []

    verdict = str(data.get("verdict", "")).upper()
    if verdict not in VERDICT_ENUM:
        return RuleResult(INSUFFICIENT_EVIDENCE,
                          f"Model returned an unusable verdict {verdict!r}; discarded.",
                          confidence=0.3, observation="")

    citations, dropped = resolve_citations(data.get("citations"), material.cells)
    if dropped:
        notes.append(f"{len(dropped)} citation(s) did not resolve and were dropped: "
                     f"{', '.join(dropped[:6])}")

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5

    statement = " ".join(str(data.get("statement", "")).split())
    observation = " ".join(str(data.get("observation", "")).split())
    enquiry = " ".join(str(data.get("enquiry", "")).split())

    if verdict == FAIL and not citations:
        verdict = INSUFFICIENT_EVIDENCE
        confidence = min(confidence, 0.5)
        notes.append("downgraded: no citation resolved, so the matter is not evidenced")

    # A pattern claimed on one or two years is not a pattern.
    try:
        spanned = int(data.get("years_reasoned_over") or 0)
    except (TypeError, ValueError):
        spanned = 0
    minimum = int(ctx.param("min_years_for_outlier", 3))
    if verdict == FAIL and 0 < spanned < minimum:
        confidence = min(confidence, 0.6)
        notes.append(f"the model reasoned over {spanned} year(s); the doctrine treats "
                     f"fewer than {minimum} as too few to call a pattern")

    prohibited = ctx.foundation.doctrine.prohibited
    lowered = f" {statement} {observation} {enquiry} ".lower()
    offending = sorted({w for w in prohibited if f" {w} " in lowered})
    if offending:
        notes.append(f"language policy: model wording contained "
                     f"{', '.join(offending)}; the client-facing sentence was withheld "
                     f"and must be redrafted")
        observation = ""

    if verdict in (PASS, NOT_APPLICABLE):
        observation = ""

    detail = {"ai": {
        "ok": True, "model": reply.model, "cached": reply.cached, "ms": reply.ms,
        "reasoning": " ".join(str(data.get("reasoning", "")).split())[:1500],
        "dropped_citations": dropped, "cells_offered": material.count,
        "years_reasoned_over": spanned, "notes": notes,
    }}
    if data.get("directive_text_found"):
        notes.append("the material contains text addressed to the reader; it was "
                     "recorded and not obeyed")
        detail["ai"]["directive_text"] = True

    if notes:
        statement = statement + " [" + "; ".join(notes) + "]"

    return RuleResult(
        verdict=verdict, statement=statement, evidence=citations,
        confidence=round(confidence, 2), detail=detail, observation=observation,
        enquiry=enquiry,
        na_reason=("determined not applicable on the cells examined"
                   if verdict == NOT_APPLICABLE else ""))


def arbitrate(rule_result: RuleResult, model_result: RuleResult) -> RuleResult:
    """Hybrid rules: the deterministic side prevails where it is definitive, and
    disagreement is recorded rather than averaged away."""
    definitive = rule_result.verdict in (PASS, FAIL)
    if not definitive:
        merged = model_result
        merged.detail = dict(merged.detail or {})
        merged.detail["arbitration"] = {
            "outcome": "model judgment used",
            "reason": f"the rule was inconclusive ({rule_result.verdict})",
            "rule_statement": rule_result.statement}
        return merged

    merged = rule_result
    merged.detail = dict(merged.detail or {})
    agrees = model_result.verdict == rule_result.verdict
    merged.detail["arbitration"] = {
        "outcome": "rule prevailed", "model_verdict": model_result.verdict,
        "agrees": agrees, "model_statement": model_result.statement}
    if not agrees:
        merged.statement += (
            f" [rule and model judgment disagree — the model said "
            f"{model_result.verdict}; the rule prevails and the disagreement is "
            f"logged for review]")
    if agrees:
        if model_result.observation and not merged.observation:
            merged.observation = model_result.observation
        if model_result.enquiry and not merged.enquiry:
            merged.enquiry = model_result.enquiry
    return merged
