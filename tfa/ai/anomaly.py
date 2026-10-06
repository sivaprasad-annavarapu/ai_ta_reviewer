"""The open comparison.

Every rule in the catalogue is a question somebody thought to ask. This pass
exists for the ones nobody thought to ask: the model is given the whole fact
matrix across the years and asked what cannot be right, with no question in
front of it.

Two guards make the output usable rather than a second opinion on everything:

  * anything the deterministic rules already found is listed to the model and
    suppressed on the way back, so the register is not doubled;
  * anything not tied to a cell handle is dropped, on the same footing as any
    other uncited claim.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..facts.matrix import Cell
from .client import ModelClient
from .prompts import (
    CITATION_PROPERTY, DIRECTIVE_PROPERTY, SYSTEM_RULES, render_findings,
    render_material, resolve_citations, select,
)

KINDS = ["contradiction between two years", "contradiction between the two documents",
         "movement without a corresponding movement elsewhere",
         "particular that stopped being reported", "figure out of proportion",
         "sequence or dating problem", "something else"]

SCAN_SCHEMA = {
    "type": "object",
    "properties": {
        "anomalies": {
            "type": "array",
            "description": "At most eight. Fewer is better than padding.",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": KINDS},
                    "statement": {"type": "string",
                                  "description": "What cannot be right, in two or "
                                                 "three sentences, naming the years "
                                                 "and the figures."},
                    "observation": {"type": "string",
                                    "description": "The same point for the client: "
                                                   "plain and factual, no "
                                                   "characterisation of any person."},
                    "enquiry": {"type": "string",
                                "description": "What to ask, or which document to call "
                                               "for, to settle it."},
                    "severity": {"type": "string", "enum": ["high", "medium", "low"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "citations": CITATION_PROPERTY,
                },
                "required": ["kind", "statement", "enquiry", "severity", "confidence",
                             "citations"],
            },
        },
        "directive_text_found": DIRECTIVE_PROPERTY,
        "coverage_note": {"type": "string",
                          "description": "What you could not examine because it was "
                                         "not in the material."},
    },
    "required": ["anomalies", "directive_text_found"],
}


@dataclass
class Anomaly:
    kind: str
    statement: str
    observation: str
    enquiry: str
    severity: str
    confidence: float
    evidence: List[Cell] = field(default_factory=list)
    dropped: List[str] = field(default_factory=list)


@dataclass
class AnomalyScan:
    ran: bool
    reason: str = ""
    found: List[Anomaly] = field(default_factory=list)
    suppressed: List[Dict] = field(default_factory=list)
    cells_compared: int = 0
    model: str = ""
    cached: bool = False
    coverage_note: str = ""


def scan(ctx, client: ModelClient, existing: Optional[Sequence] = None) -> AnomalyScan:
    if client is None or not client.available:
        reason = (client.settings.unavailable_reason() if client
                  else "no model client was configured for this run")
        return AnomalyScan(ran=False, reason=reason)

    if not ctx.matrix.years:
        return AnomalyScan(ran=False,
                           reason="no assessment year could be read, so there is "
                                  "nothing to compare")

    selection = select(ctx.matrix)
    material = render_material(ctx.matrix, selection, trends=ctx.trends,
                               extra=render_findings(existing or []))

    prompt = f"""\
WHAT YOU ARE LOOKING AT
The complete set of figures this system extracted from {len(ctx.matrix.years)} \
assessment year(s) of one assessee's income-tax returns and tax audit reports: \
{', '.join(ctx.matrix.years)}.

WHAT TO DO
No question has been written for this pass. Read the years against each other and \
report what cannot be right, or what would need an explanation before it could be \
relied on. Look in particular for:

  - a figure that must equal another figure and does not;
  - a closing position that is not the next year's opening position;
  - a movement in one series with no corresponding movement in the series that \
would have to move with it;
  - a particular reported for several years that stops;
  - a proportion that is out of keeping with the size of the business;
  - dates that cannot be in the order they are in.

Report at most eight. An empty list is a perfectly good answer, and much better \
than eight weak ones. Do not repeat anything in <already_tested>: those are \
settled, and your value here is what they missed.

{material.text}

AVAILABLE CELL HANDLES: {material.handles}

Report only what you can cite.
"""

    reply = client.structured(
        system=SYSTEM_RULES, user=prompt, schema=SCAN_SCHEMA,
        tool_name="record_anomalies",
        tool_description="Record anomalies found by open comparison of the years.",
        label="open-comparison", max_tokens=4000)

    if reply.failed:
        return AnomalyScan(ran=False, reason=reply.reason,
                           cells_compared=material.count)

    data = reply.data or {}
    found: List[Anomaly] = []
    suppressed: List[Dict] = []
    already = {f.rule_id for f in (existing or []) if f.verdict == "FAIL"}
    already_facts = {fact for f in (existing or []) if f.verdict == "FAIL"
                     for fact in f.facts_used}

    for item in data.get("anomalies", [])[:8]:
        citations, dropped = resolve_citations(item.get("citations"), material.cells)
        statement = " ".join(str(item.get("statement", "")).split())
        if not citations:
            suppressed.append({"statement": statement[:200],
                               "reason": "no citation resolved to a cell in the matrix"})
            continue

        prohibited = ctx.foundation.doctrine.prohibited
        lowered = f" {statement} {item.get('observation', '')} ".lower()
        offending = sorted({w for w in prohibited if f" {w} " in lowered})
        if offending:
            suppressed.append({
                "statement": statement[:200],
                "reason": f"wording contained {', '.join(offending)}, which the "
                          f"language policy does not permit in a finding"})
            continue

        # Where every cell cited belongs to a fact a deterministic rule already
        # failed on, this is the same matter arriving twice.
        cited_facts = {c.fact_id for c in citations}
        if cited_facts and cited_facts <= already_facts:
            suppressed.append({
                "statement": statement[:200],
                "reason": "every fact cited is already the subject of a deterministic "
                          "finding"})
            continue

        try:
            confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
        except (TypeError, ValueError):
            confidence = 0.5

        found.append(Anomaly(
            kind=str(item.get("kind", "something else")),
            statement=statement,
            observation=" ".join(str(item.get("observation", "")).split()),
            enquiry=" ".join(str(item.get("enquiry", "")).split()),
            severity=str(item.get("severity", "medium")).lower(),
            confidence=round(confidence, 2), evidence=citations, dropped=dropped))

    return AnomalyScan(
        ran=True, found=found, suppressed=suppressed, cells_compared=material.count,
        model=reply.model, cached=reply.cached,
        coverage_note=" ".join(str(data.get("coverage_note", "")).split()))
