"""The factual brief: the only thing the inference layer is allowed to see.

This is the contract between the two halves of the system, and the reason the
inference is worth anything.

Below this module, everything is arithmetic: located figures, year-on-year
movement, tie-outs, thresholds crossed. Above it, a model is asked one bounded
question — is this pattern explicable, and by what — against a brief that
Python wrote. The model gets no raw JSON, no free run of the matrix and no
licence to choose its own evidence: it gets the same series a person is looking
at on the chart, abstracted, with the deterministic findings already settled
and the declared background of the years attached.

That ordering is the product. A model handed the whole file will write
something plausible about anything; a model handed a brief can only answer the
question the brief poses, and every sentence it writes can be checked against
a cell.

A brief carries four things:

    the series          values by year, with the movement already computed
    what is settled     the deterministic findings on the same subject
    the background      what else was true in those years, from the pinned
                        context file — never from the model's memory
    the question        one question, with the standard for answering it
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from ..facts.matrix import Cell, FactMatrix
from ..foundation import years as Y
from ..foundation.hashing import hash_file
from ..rules.context import money


# ------------------------------------------------------------------ context
@dataclass(frozen=True)
class Period:
    ay: str
    fy: str
    conditions: str
    factors: List[str]
    filing_notes: str = ""
    statutory_changes: str = ""
    verified: bool = False


@dataclass
class PeriodContext:
    version: str
    path: Path
    digest: str
    status: str
    factors: Dict[str, str]
    periods: Dict[str, Period]
    calendar: Dict[str, Dict]

    def for_years(self, years: Sequence[str]) -> List[Period]:
        return [self.periods[ay] for ay in years if ay in self.periods]

    def unknown(self, years: Sequence[str]) -> List[str]:
        return [ay for ay in years if ay not in self.periods]

    def manifest(self) -> Dict:
        return {"version": self.version, "digest": self.digest,
                "status": self.status, "periods": len(self.periods),
                "factors": sorted(self.factors)}


def load_context(path: Optional[Path] = None) -> PeriodContext:
    path = path or default_context_path()
    raw = yaml.safe_load(path.read_text())
    periods: Dict[str, Period] = {}
    for p in raw.get("periods", []):
        ay = Y.normalise(p["ay"]) or p["ay"]
        factors = list(p.get("factors", []) or []) + list(p.get("factors_extra", []) or [])
        periods[ay] = Period(
            ay=ay, fy=p.get("fy", Y.fy_label(ay) if Y.normalise(ay) else ""),
            conditions=" ".join((p.get("conditions") or "").split()),
            factors=factors,
            filing_notes=" ".join((p.get("filing_notes") or "").split()),
            statutory_changes=" ".join((p.get("statutory_changes") or "").split()),
            verified=bool(p.get("verified", False)))
    calendar = {}
    for c in raw.get("calendar", []):
        ay = Y.normalise(c["ay"]) or c["ay"]
        calendar[ay] = {k: v for k, v in c.items() if k != "ay"}
    return PeriodContext(
        version=str(raw["version"]), path=path, digest=hash_file(path),
        status=raw.get("verification_status", "unverified_draft"),
        factors={f["id"]: f["label"] for f in raw.get("factors", [])},
        periods=periods, calendar=calendar)


def default_context_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "context").glob("*-v*.yaml"))
    if not candidates:
        raise FileNotFoundError("no period context file found")
    return candidates[-1]


# -------------------------------------------------------------------- brief
@dataclass
class BriefSeries:
    fact: str
    label: str
    unit: str
    values: Dict[str, float]
    refs: Dict[str, str]
    movement: List[Dict] = field(default_factory=list)
    shape: str = ""
    cagr: Optional[float] = None
    largest: Optional[Dict] = None
    gaps: List[str] = field(default_factory=list)

    def render(self) -> str:
        """One line a person could read aloud, and the model must reason from."""
        unit = self.unit
        cells = "; ".join(
            f"{ay.replace('AY ', '')} {_fmt(v, unit)} [{self.refs.get(ay, '')}]"
            for ay, v in self.values.items())
        tail = []
        if self.cagr is not None:
            tail.append(f"compound {self.cagr * 100:+.1f}%/yr")
        if self.largest and self.largest.get("pct") is not None:
            tail.append(f"largest move {self.largest['pct'] * 100:+.0f}% in "
                        f"{self.largest['ay'].replace('AY ', '')}")
        if self.shape:
            tail.append(self.shape)
        if self.gaps:
            tail.append(f"no figure for {', '.join(g.replace('AY ', '') for g in self.gaps)}")
        return f"  {self.label}: {cells}" + (f"   ({'; '.join(tail)})" if tail else "")


@dataclass
class Brief:
    """What is handed up to the inference layer, and kept for the record."""
    topic: str
    title: str
    years: List[str]
    series: List[BriefSeries]
    settled: List[Dict]                       # deterministic findings on the topic
    background: List[Period]
    calendar: Dict[str, Dict]
    charts: List[str]
    cells: List[Cell] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def substantive(self) -> bool:
        """Enough to reason over: at least one series with two observations."""
        return any(len(s.values) >= 2 for s in self.series)

    @property
    def handles(self) -> str:
        return ", ".join(c.ref for c in self.cells)

    def render(self) -> str:
        lines = [f"<brief topic=\"{self.topic}\">",
                 f"{self.title}. Assessment years: "
                 f"{', '.join(y.replace('AY ', '') for y in self.years) or 'none'}.",
                 "",
                 "THE SERIES, AS EXTRACTED AND MEASURED BY THIS SYSTEM",
                 "Each figure carries the handle of the cell it was read from. Cite "
                 "those handles and nothing else."]
        for s in self.series:
            lines.append(s.render())
        if not self.series:
            lines.append("  (no series in this topic carried a figure)")

        lines += ["", "WHAT THE DETERMINISTIC RULES HAVE ALREADY SETTLED",
                  "These were decided by arithmetic, not judgment. Treat them as "
                  "settled. Do not restate them as your own findings; reason about "
                  "what they mean together."]
        if self.settled:
            for f in self.settled:
                lines.append(f"  [{f['instance_id']}] {f['statement']}")
        else:
            lines.append("  (nothing was raised by rule on this topic)")

        lines += ["", "WHAT ELSE WAS TRUE IN THOSE YEARS",
                  "From this firm's pinned context file. You may correlate against "
                  "these entries. You may not assert any other fact about a year, "
                  "an industry or an economy."]
        if self.background:
            for p in self.background:
                lines.append(f"  {p.ay} ({p.fy}): {p.conditions}"
                             + (f" STATUTORY: {p.statutory_changes}"
                                if p.statutory_changes else "")
                             + (f" FILING: {p.filing_notes}" if p.filing_notes else "")
                             + ("" if p.verified else "  [not yet verified by a person]"))
        else:
            lines.append("  (no context is on file for these years, so no correlation "
                         "with conditions outside the filings can be drawn)")

        if self.calendar:
            lines.append("")
            lines.append("DUE DATES ON FILE")
            for ay, entry in self.calendar.items():
                bits = ", ".join(f"{k.replace('_', ' ')} {v}"
                                 for k, v in entry.items() if k != "verified")
                lines.append(f"  {ay}: {bits}")

        for note in self.notes:
            lines.append(f"\nNOTE: {note}")
        lines.append("</brief>")
        return "\n".join(lines)

    def as_dict(self) -> Dict:
        return {
            "topic": self.topic, "title": self.title, "years": self.years,
            "charts": self.charts,
            "series": [{"fact": s.fact, "label": s.label, "unit": s.unit,
                        "values": s.values, "shape": s.shape,
                        "cagr": s.cagr, "largest": s.largest, "gaps": s.gaps}
                       for s in self.series],
            "settled": self.settled,
            "background": [{"ay": p.ay, "fy": p.fy, "conditions": p.conditions,
                            "statutory_changes": p.statutory_changes,
                            "verified": p.verified} for p in self.background],
            "substantive": self.substantive,
            "cells": len(self.cells),
            "text": self.render(),
        }


def _fmt(value: Optional[float], unit: str) -> str:
    if value is None:
        return "not available"
    if unit == "pct":
        return f"{value:.2f}%"
    if unit == "days":
        return f"{value:.0f} days"
    if unit == "count":
        return f"{value:,.0f}"
    return money(value)


def build_brief(topic: str, title: str, facts: Sequence[str], matrix: FactMatrix,
                trends: Dict, findings: Sequence, context: PeriodContext,
                charts: Optional[Sequence[str]] = None,
                rules: Optional[Sequence[str]] = None,
                themes: Optional[Sequence[str]] = None) -> Brief:
    """Assemble one brief. Everything in it is measured before the model sees it."""
    series: List[BriefSeries] = []
    cells: List[Cell] = []
    for fact in facts:
        spec = matrix.spec(fact)
        if spec is None:
            continue
        values = matrix.series(fact)
        if not values:
            continue
        refs: Dict[str, str] = {}
        for ay in values:
            cell = matrix.get(fact, ay)
            if cell:
                refs[ay] = cell.ref
                cells.append(cell)
        trend = trends.get(fact)
        movement, shape, cagr, largest, gaps = [], "", None, None, []
        if trend is not None:
            movement = [s.as_dict() for s in trend.steps]
            shape = trend.direction(0.30)
            cagr = trend.cagr
            largest = trend.largest_step.as_dict() if trend.largest_step else None
            gaps = trend.gaps
        series.append(BriefSeries(
            fact=fact, label=spec.label, unit=("pct" if spec.kind == "ratio"
                                               else "count" if spec.kind == "count"
                                               else "inr"),
            values=values, refs=refs, movement=movement, shape=shape,
            cagr=cagr, largest=largest, gaps=gaps))

    settled = [
        {"instance_id": f.instance_id, "rule_id": f.rule_id, "ay": f.ay,
         "statement": f.statement, "amount": f.amount}
        for f in findings
        if f.verdict == "FAIL"
        and ((rules and f.rule_id in rules)
             or (themes and f.theme in themes)
             or (not rules and not themes
                 and any(fact in facts for fact in (f.facts_used or []))))
    ]

    brief = Brief(topic=topic, title=title, years=list(matrix.years), series=series,
                  settled=settled, background=context.for_years(matrix.years),
                  calendar={ay: context.calendar[ay] for ay in matrix.years
                            if ay in context.calendar},
                  charts=list(charts or []), cells=cells)

    unknown = context.unknown(matrix.years)
    if unknown:
        brief.notes.append(
            f"No context is on file for {', '.join(unknown)}, so nothing outside the "
            f"filings may be correlated with those years.")
    if context.status != "verified":
        brief.notes.append(
            "The context file is an unverified draft. An inference resting on it must "
            "say so.")
    return brief
