"""The inference layer.

The only place a model is asked a question in this system, and it is asked in
one shape: here is a brief Python wrote, here is the provision quoted from the
pack, here is the declared background of those years — identify the
aberrations, say what corroborates them, and say what would settle it.

What the model returns is not trusted. The enum is checked, the citations are
resolved against the cells that were actually offered, a correlation drawn to a
factor the context file does not carry is dropped, prohibited wording is
withheld, and a conclusion with nothing cited is downgraded. What survives is
an inference every sentence of which can be checked against a chart a person is
looking at.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from ..analysis.brief import Brief, PeriodContext, build_brief
from ..facts.matrix import Cell, FactMatrix
from ..foundation.errors import CitationError
from ..foundation.hashing import hash_file
from .client import ModelClient
from .prompts import CITATION_PROPERTY, DIRECTIVE_PROPERTY, SYSTEM_RULES

VERDICTS = ["REPORTABLE", "EXPLAINED", "NOTHING_ARISING", "INSUFFICIENT_EVIDENCE"]

SYSTEM_INFERENCE = SYSTEM_RULES + """
7. YOU ARE THE INFERENCE LAYER, NOT THE MEASUREMENT LAYER. The figures, the \
movements and the findings in the brief were computed by program. Do not \
recompute them, do not correct them, and do not contradict them. Your work is \
what they mean together.

8. CORRELATE ONLY WITH WHAT IS ON FILE. You may relate a movement to an entry \
in the background section of the brief. You may not assert any other fact about \
a year, an industry, a commodity, an economy or the assessee. Where no entry \
covers the year, say that no correlation can be drawn.

9. OFFER A CAUSE AS A HYPOTHESIS, WITH ITS TEST. Where you think you know why \
something moved, say what would confirm it and what would refute it. An \
explanation nobody can check is not worth writing down.

10. NAME THE DOCUMENT. Every hypothesis carries the record that would settle \
it: the working paper, the ledger, the challan, the reconciliation. The audit \
programme is built from those by program, so a hypothesis with no document \
named is a hypothesis nobody can act on.
"""

INFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {
            "type": "string", "enum": VERDICTS,
            "description": "REPORTABLE where the series show something that needs to "
                           "be put to the client. EXPLAINED where a movement is real "
                           "but the brief accounts for it. NOTHING_ARISING where the "
                           "series are unremarkable. INSUFFICIENT_EVIDENCE where too "
                           "few years or too few series carry figures.",
        },
        "headline": {"type": "string",
                     "description": "One sentence, for a reader who will read only "
                                    "one. Name the years."},
        "aberrations": {
            "type": "array",
            "description": "At most four. Each must cite the cells it rests on.",
            "items": {
                "type": "object",
                "properties": {
                    "ay": {"type": "string", "description": "The year, e.g. 2024-25"},
                    "series": {"type": "string",
                               "description": "Which series departs from its trend"},
                    "what": {"type": "string",
                             "description": "What departs, in one or two sentences"},
                    "corroborated": {
                        "type": "string",
                        "enum": ["corroborated", "contradicted", "neither"],
                        "description": "Whether the other series for the same year "
                                       "move with it, against it, or are silent",
                    },
                    "corroboration_detail": {
                        "type": "string",
                        "description": "Which series should have moved, and did or "
                                       "did not",
                    },
                    "citations": CITATION_PROPERTY,
                },
                "required": ["ay", "series", "what", "corroborated", "citations"],
            },
        },
        "correlations": {
            "type": "array",
            "description": "Only against factors named in the brief's background.",
            "items": {
                "type": "object",
                "properties": {
                    "factor": {"type": "string",
                               "description": "The factor id from the brief, or "
                                              "'none' where nothing on file explains "
                                              "it"},
                    "ay": {"type": "string"},
                    "effect": {"type": "string",
                               "description": "How the factor and the movement relate"},
                    "citations": CITATION_PROPERTY,
                },
                "required": ["factor", "effect"],
            },
        },
        "hypotheses": {
            "type": "array",
            "minItems": 1,
            "description": "Candidate explanations, each with its test. This is the "
                           "part a reviewer acts on, and it is not optional: where "
                           "the verdict is REPORTABLE give at least two, and where it "
                           "is EXPLAINED give the one the brief supports, with what "
                           "would refute it. Never leave this empty.",
            "items": {
                "type": "object",
                "properties": {
                    "explanation": {"type": "string"},
                    "confirms_if": {"type": "string",
                                    "description": "What, if found, would confirm it"},
                    "refutes_if": {"type": "string",
                                   "description": "What, if found, would refute it"},
                    "document": {"type": "string",
                                 "description": "The document or record to call for"},
                },
                "required": ["explanation", "confirms_if"],
            },
        },
        "observation": {"type": "string",
                        "description": "The point written for the client: plain, "
                                       "factual, no rule names, no characterisation "
                                       "of any person. Empty unless REPORTABLE."},
        "vulnerability": {"type": "string",
                          "description": "What this exposes if left as it stands. "
                                         "Internal."},
        "audit_focus": {
            "type": "array",
            "description": "Optional. An area a tax audit should cover that the "
                           "hypotheses above do not already imply. Leave empty where "
                           "the hypotheses cover it: the audit programme is assembled "
                           "from them by program, and a restatement adds nothing.",
            "items": {
                "type": "object",
                "properties": {
                    "area": {"type": "string",
                             "description": "The area, e.g. 'gross margin and stock "
                                            "valuation'"},
                    "why": {"type": "string"},
                    "procedure": {"type": "string",
                                  "description": "What to actually do"},
                },
                "required": ["area", "why"],
            },
        },
        "years_reasoned_over": {"type": "integer"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "citations": CITATION_PROPERTY,
        "directive_text_found": DIRECTIVE_PROPERTY,
    },
    "required": ["verdict", "headline", "aberrations", "hypotheses", "confidence",
                 "years_reasoned_over", "citations", "directive_text_found"],
}


# ---------------------------------------------------------------- catalogue
@dataclass(frozen=True)
class InferenceSpec:
    id: str
    topic: str
    title: str
    question: str
    rubric: str
    facts: List[str]
    charts: List[str]
    settled_themes: List[str]
    correlate_with: List[str]
    authority: List[str]
    severity: str = "medium"
    model: str = ""
    effort: str = ""


@dataclass
class InferenceCatalogue:
    version: str
    path: Path
    digest: str
    topics: Dict[str, str]
    inferences: List[InferenceSpec]

    def by_id(self, inference_id: str) -> Optional[InferenceSpec]:
        return next((i for i in self.inferences if i.id == inference_id), None)

    @property
    def counts(self) -> Dict[str, int]:
        return {"declared": len(self.inferences),
                "topics": len({i.topic for i in self.inferences}),
                "high": sum(1 for i in self.inferences if i.severity == "high")}


def load_inferences(path: Optional[Path] = None, pack=None, fact_map=None,
                    charts=None, context: Optional[PeriodContext] = None
                    ) -> InferenceCatalogue:
    """Validated against everything else before a single call is made: a
    question that reads a fact we do not extract, cites law the pack does not
    carry, or correlates against a factor the context file does not declare, is
    a defect in our method."""
    path = path or default_inference_path()
    raw = yaml.safe_load(path.read_text())
    topics = {t["id"]: t["label"] for t in raw.get("topics", [])}
    out: List[InferenceSpec] = []
    seen = set()
    for i in raw.get("inferences", []):
        iid = i["id"]
        if iid in seen:
            raise CitationError(f"duplicate inference id {iid}")
        seen.add(iid)
        if i.get("topic") not in topics:
            raise CitationError(f"inference {iid} names unknown topic {i.get('topic')!r}")
        if not i.get("rubric"):
            raise CitationError(
                f"inference {iid} puts a question to a model with no rubric — a "
                f"question with no standard to answer against is how a model comes to "
                f"invent one")
        for provision in i.get("authority", []) or []:
            if pack is not None:
                pack.resolve(provision)
        for fact in i.get("facts", []) or []:
            if fact_map is not None and not fact_map.by_id(fact):
                raise CitationError(
                    f"inference {iid} reads {fact!r}, which is not a fact in the "
                    f"dictionary")
        for chart in i.get("charts", []) or []:
            if charts is not None and charts.by_id(chart) is None:
                raise CitationError(
                    f"inference {iid} points at chart {chart!r}, which is not in the "
                    f"chart catalogue")
        for factor in i.get("correlate_with", []) or []:
            if context is not None and factor not in context.factors:
                raise CitationError(
                    f"inference {iid} may correlate with {factor!r}, which the context "
                    f"pack does not declare")
        out.append(InferenceSpec(
            id=iid, topic=i["topic"], title=i["title"],
            question=" ".join(i["question"].split()),
            rubric=" ".join(i["rubric"].split()),
            facts=list(i.get("facts", []) or []),
            charts=list(i.get("charts", []) or []),
            settled_themes=list(i.get("settled_themes", []) or []),
            correlate_with=list(i.get("correlate_with", []) or []),
            authority=list(i.get("authority", []) or []),
            severity=i.get("severity", "medium"),
            model=i.get("model", "") or "", effort=i.get("effort", "") or ""))
    return InferenceCatalogue(version=str(raw["version"]), path=path,
                              digest=hash_file(path), topics=topics, inferences=out)


def default_inference_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "inference").glob("canonical-v*.yaml"))
    if not candidates:
        raise CitationError("no inference catalogue found")
    return candidates[-1]


# ----------------------------------------------------------------- results
@dataclass
class Inference:
    spec_id: str
    topic: str
    title: str
    question: str
    verdict: str
    headline: str
    confidence: float
    aberrations: List[Dict] = field(default_factory=list)
    correlations: List[Dict] = field(default_factory=list)
    hypotheses: List[Dict] = field(default_factory=list)
    audit_focus: List[Dict] = field(default_factory=list)
    observation: str = ""
    vulnerability: str = ""
    evidence: List[Cell] = field(default_factory=list)
    charts: List[str] = field(default_factory=list)
    authority: List[Dict] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    brief: Optional[Dict] = None
    severity: str = "medium"
    ran: bool = True
    reason: str = ""
    model: str = ""
    cached: bool = False
    years_reasoned_over: int = 0

    @property
    def reportable(self) -> bool:
        return self.verdict == "REPORTABLE"

    def as_dict(self, include_brief: bool = False) -> Dict:
        out = {
            "id": self.spec_id, "topic": self.topic, "title": self.title,
            "question": self.question, "verdict": self.verdict,
            "headline": self.headline, "confidence": self.confidence,
            "aberrations": self.aberrations, "correlations": self.correlations,
            "hypotheses": self.hypotheses, "audit_focus": self.audit_focus,
            "observation": self.observation, "vulnerability": self.vulnerability,
            "evidence": [c.render() for c in self.evidence],
            "evidence_cells": [{"fact": c.fact_id, "ay": c.ay, "value": c.value,
                                "pointer": c.pointer, "file": c.file, "ref": c.ref}
                               for c in self.evidence],
            "charts": self.charts, "authority": [a["citation"] for a in self.authority],
            "notes": self.notes, "severity": self.severity, "ran": self.ran,
            "reason": self.reason, "model": self.model, "cached": self.cached,
            "years_reasoned_over": self.years_reasoned_over,
        }
        if include_brief:
            out["brief"] = self.brief
        return out


def _authority_brief(spec: InferenceSpec, foundation) -> str:
    lines = []
    for provision_id in spec.authority:
        p = foundation.pack.resolve(provision_id)
        if p.id not in foundation.provisions_in_force:
            continue
        instrument = foundation.pack.instruments.get(p.instrument, p.instrument)
        lines.append(f"- {p.reference} ({instrument}) — {p.title}. {p.summary}")
    return "\n".join(lines) or "(this question is analytical; no provision is quoted)"


def run_inference(spec: InferenceSpec, brief: Brief, foundation, client: ModelClient,
                  context: PeriodContext) -> Inference:
    authority = [
        {"id": p.id, "citation": p.citation, "title": p.title}
        for p in (foundation.pack.resolve(a) for a in spec.authority)
        if p.id in foundation.provisions_in_force
    ]
    base = Inference(
        spec_id=spec.id, topic=spec.topic, title=spec.title, question=spec.question,
        verdict="INSUFFICIENT_EVIDENCE", headline="", confidence=0.0,
        charts=list(spec.charts), authority=authority, severity=spec.severity,
        brief=brief.as_dict())

    if not brief.substantive:
        base.ran = False
        base.reason = ("no series in this topic carries two years of figures, so there "
                       "is nothing to infer from")
        base.headline = base.reason
        return base

    if client is None or not client.available:
        base.ran = False
        base.reason = (client.settings.unavailable_reason() if client
                       else "no model client was configured for this run")
        base.headline = f"Not assessed: {base.reason}."
        return base

    factors = "\n".join(f"  {fid}: {context.factors[fid]}"
                        for fid in spec.correlate_with if fid in context.factors)

    prompt = f"""\
THE QUESTION
{spec.question}

HOW TO ANSWER IT
{spec.rubric}

THE PROVISIONS THAT BEAR ON IT, QUOTED FROM THIS FIRM'S PINNED PACK
{_authority_brief(spec, foundation)}

FACTORS YOU MAY CORRELATE AGAINST
{factors or '  (none declared for this question)'}

THE ASSESSEE, AS DECLARED BY THE FIRM
- Name: {foundation.client.display_name}
- Constitution: {foundation.client.entity_type or 'not declared'}
- Business: {foundation.client.nature_of_business or 'not declared'}
{'- NOTE: nothing was declared about the assessee; what is above was read from the filings themselves.' if not foundation.client.declared else ''}

The reader of your answer is looking at a chart of these same series while they
read it. Every figure you mention must be one of the figures below, and every
claim must cite the handle beside it.

{brief.render()}

AVAILABLE CELL HANDLES: {brief.handles}

Answer the question.
"""

    reply = client.structured(
        system=SYSTEM_INFERENCE, user=prompt, schema=INFERENCE_SCHEMA,
        tool_name="record_inference",
        tool_description="Record the inference, with citations to the cells provided.",
        label=spec.id, model=spec.model or None, effort=spec.effort or None,
        max_tokens=4000)

    if reply.failed:
        base.ran = False
        base.reason = reply.reason
        base.headline = f"Not assessed: {reply.reason}."
        return base

    return _validate(spec, brief, reply, foundation, context, authority)


def _validate(spec: InferenceSpec, brief: Brief, reply, foundation,
              context: PeriodContext, authority: List[Dict]) -> Inference:
    from .prompts import resolve_citations

    data = reply.data or {}
    notes: List[str] = []

    verdict = str(data.get("verdict", "")).upper()
    if verdict not in VERDICTS:
        return Inference(
            spec_id=spec.id, topic=spec.topic, title=spec.title,
            question=spec.question, verdict="INSUFFICIENT_EVIDENCE",
            headline="The model returned an unusable verdict; the answer was discarded.",
            confidence=0.0, charts=list(spec.charts), authority=authority,
            severity=spec.severity, ran=True, reason=f"verdict {verdict!r}",
            brief=brief.as_dict(), model=reply.model, cached=reply.cached)

    citations, dropped = resolve_citations(data.get("citations"), brief.cells)
    for block in ("aberrations", "correlations"):
        for item in data.get(block, []) or []:
            kept, lost = resolve_citations(item.get("citations"), brief.cells)
            item["evidence"] = [c.render() for c in kept]
            item["refs"] = [c.ref for c in kept]
            dropped.extend(lost)
            for cell in kept:
                if cell not in citations:
                    citations.append(cell)
    if dropped:
        notes.append(f"{len(dropped)} citation(s) did not resolve to a cell in the "
                     f"brief and were dropped: {', '.join(sorted(set(dropped))[:6])}")

    # A correlation drawn to something the context file does not carry is not a
    # correlation, it is a recollection.
    allowed = set(spec.correlate_with) | {"none"}
    correlations = []
    for item in data.get("correlations", []) or []:
        factor = str(item.get("factor", "")).strip().lower().replace(" ", "_")
        if factor not in allowed:
            notes.append(f"a correlation with {item.get('factor')!r} was dropped: the "
                         f"context pack does not declare that factor for this question")
            continue
        item["factor"] = factor
        item["factor_label"] = context.factors.get(factor, "nothing on file")
        correlations.append(item)

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    try:
        spanned = int(data.get("years_reasoned_over") or 0)
    except (TypeError, ValueError):
        spanned = 0

    minimum = int(foundation.doctrine.param("min_years_for_outlier", 3))
    if verdict == "REPORTABLE" and 0 < spanned < minimum:
        confidence = min(confidence, 0.6)
        notes.append(f"the answer reasons over {spanned} year(s); the doctrine treats "
                     f"fewer than {minimum} as too few to call a pattern")

    headline = " ".join(str(data.get("headline", "")).split())
    observation = " ".join(str(data.get("observation", "")).split())
    vulnerability = " ".join(str(data.get("vulnerability", "")).split())

    if verdict == "REPORTABLE" and not (data.get("hypotheses") or []):
        notes.append("the answer names no candidate explanation, so there is nothing "
                     "here for a reviewer to test; treat the observation as "
                     "provisional")
    if verdict == "REPORTABLE" and not citations:
        verdict = "INSUFFICIENT_EVIDENCE"
        confidence = min(confidence, 0.5)
        notes.append("downgraded: nothing cited resolved to a cell, so the inference "
                     "is not evidenced")

    prohibited = foundation.doctrine.prohibited
    lowered = f" {headline} {observation} {vulnerability} ".lower()
    offending = sorted({w for w in prohibited if f" {w} " in lowered})
    if offending:
        notes.append(f"language policy: the wording contained {', '.join(offending)}; "
                     f"the client-facing sentence was withheld and must be redrafted")
        observation = ""

    if verdict != "REPORTABLE":
        observation = ""

    if data.get("directive_text_found"):
        notes.append("the material contains text addressed to the reader; it was "
                     "recorded and not obeyed")
    if context.status != "verified":
        notes.append("the period context this correlation rests on is an unverified "
                     "draft")

    return Inference(
        spec_id=spec.id, topic=spec.topic, title=spec.title, question=spec.question,
        verdict=verdict, headline=headline, confidence=round(confidence, 2),
        aberrations=(data.get("aberrations") or [])[:4],
        correlations=correlations,
        hypotheses=(data.get("hypotheses") or [])[:5],
        audit_focus=(data.get("audit_focus") or [])[:5],
        observation=observation, vulnerability=vulnerability,
        evidence=citations, charts=list(spec.charts), authority=authority,
        notes=notes, brief=brief.as_dict(), severity=spec.severity, ran=True,
        model=reply.model, cached=reply.cached, years_reasoned_over=spanned)


def infer_all(catalogue: InferenceCatalogue, matrix: FactMatrix, trends: Dict,
              findings: Sequence, foundation, client: ModelClient,
              context: PeriodContext, on_result=None) -> List[Inference]:
    results: List[Inference] = []
    for spec in catalogue.inferences:
        brief = build_brief(
            topic=spec.topic, title=spec.title, facts=spec.facts, matrix=matrix,
            trends=trends, findings=findings, context=context, charts=spec.charts,
            themes=spec.settled_themes)
        result = run_inference(spec, brief, foundation, client, context)
        results.append(result)
        if on_result:
            on_result(result)
    return results
