"""The report that leaves the firm.

Two things happen here. The findings are grouped into a narrative a client can
read — observations, not rule identifiers, not rubrics, not severities — and
then the result is put through a gate that refuses to release anything carrying
the firm's method or a word the language policy forbids.

The gate is not decoration. It is the difference between a system that produces
a report and a system that can be allowed to send one.
"""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from ..rules.context import money

# Identifiers of our own method. None of these may appear in released text.
ID_PATTERN = re.compile(r"\b(?:ATT|IDN|TIE|DEP|STK|STA|LOS|RAT|ARI|GST|PAR|TRD|EXP|"
                        r"JDG|ANM)-\d{3}\b")
INTERNAL_WORDS = ("rubric", "fact matrix", "canonical rule", "confidence",
                  "verdict", "severity", "threshold")


@dataclass
class Paragraph:
    theme: str
    heading: str
    text: str
    years: List[str] = field(default_factory=list)
    references: List[str] = field(default_factory=list)
    authority: List[str] = field(default_factory=list)
    enquiries: List[str] = field(default_factory=list)
    sources: int = 0
    amount: Optional[float] = None


@dataclass
class Gate:
    passed: bool
    summary: str
    leaks: List[str] = field(default_factory=list)
    prohibited: List[str] = field(default_factory=list)
    unobserved: List[str] = field(default_factory=list)


@dataclass
class Narrative:
    scope: str
    coverage: str
    paragraphs: List[Paragraph]
    gate: Gate
    exposure: float = 0.0
    enquiries: List[Dict] = field(default_factory=list)


THEME_HEADINGS = {
    "coverage": "The records provided",
    "identity": "Identity of the assessee and of the filings",
    "tie_out": "The returns against the audit reports",
    "continuity": "Figures carried from one year to the next",
    "arithmetic": "Internal arithmetic of the particulars",
    "particulars": "Particulars answered against the amounts reported",
    "trend": "Movements across the years",
    "exposure": "Amounts reported that carry a consequence",
    "judgment": "Matters requiring judgment",
    "open": "Matters found by comparing the years against each other",
    "inference": "What the years suggest when read together",
}


def synthesise(result, client_name: str = "", years: Optional[Sequence[str]] = None,
               prohibited: Optional[Sequence[str]] = None) -> Narrative:
    years = list(years or result.matrix.years)
    attendance = result.filings.attendance

    scope = (
        f"We have analysed the income-tax returns and tax audit reports provided for "
        f"{client_name or 'the assessee'} for "
        f"{('the ' + str(len(years)) + ' assessment years ' + years[0] + ' to ' + years[-1]) if len(years) > 1 else (years[0] if years else 'no assessment year')}"
        f". The analysis is of those documents alone: we have not examined the books "
        f"of account, the underlying records, or any assessment or appellate order.")

    coverage = attendance.statement + "."

    by_theme: Dict[str, List] = {}
    for f in result.findings:
        if f.verdict != "FAIL":
            continue
        by_theme.setdefault(f.theme, []).append(f)
    for a in (getattr(result, "anomalies", None).found
              if getattr(result, "anomalies", None) and result.anomalies.ran else []):
        by_theme.setdefault("open", []).append(a)

    paragraphs: List[Paragraph] = []
    enquiries: List[Dict] = []
    for theme, items in by_theme.items():
        sentences: List[str] = []
        references: List[str] = []
        authority: List[str] = []
        theme_years: List[str] = []
        total = 0.0
        for item in items:
            observation = (getattr(item, "observation", "") or "").strip()
            if not observation:
                continue
            if observation not in sentences:
                sentences.append(observation)
            references.extend(getattr(item, "evidence", []) or [])
            for a in (getattr(item, "authority", []) or []):
                citation = a["citation"] if isinstance(a, dict) else str(a)
                if citation not in authority:
                    authority.append(citation)
            ay = getattr(item, "ay", None)
            if ay and ay not in theme_years:
                theme_years.append(ay)
            total += getattr(item, "amount", None) or 0
            if getattr(item, "enquiry", ""):
                enquiries.append({
                    "theme": theme,
                    "heading": THEME_HEADINGS.get(theme, theme),
                    "ay": ay,
                    "matter": observation or getattr(item, "statement", "")[:200],
                    "enquiry": item.enquiry,
                    "severity": getattr(item, "severity", "medium"),
                    "amount": getattr(item, "amount", None),
                })
        if not sentences:
            continue
        paragraphs.append(Paragraph(
            theme=theme, heading=THEME_HEADINGS.get(theme, theme),
            text=" ".join(sentences),
            years=theme_years,
            references=sorted(set(references))[:12],
            authority=authority[:8],
            sources=len(items), amount=(total or None)))

    order = list(THEME_HEADINGS)
    paragraphs.sort(key=lambda p: order.index(p.theme) if p.theme in order else 99)

    gate = _gate(scope, coverage, paragraphs, result, prohibited or [])
    return Narrative(scope=scope, coverage=coverage, paragraphs=paragraphs, gate=gate,
                     exposure=sum(p.amount or 0 for p in paragraphs),
                     enquiries=enquiries)


def _gate(scope: str, coverage: str, paragraphs: Sequence[Paragraph], result,
          prohibited_words: Sequence[str]) -> Gate:
    body = " ".join([scope, coverage] + [p.text for p in paragraphs])
    leaks = sorted(set(ID_PATTERN.findall(body)))
    lowered = f" {body.lower()} "
    prohibited = sorted({w for w in prohibited_words if f" {w} " in lowered})
    internal = sorted({w for w in INTERNAL_WORDS if w in lowered})
    leaks.extend(internal)

    # A FAIL with no client-facing sentence is a matter the report does not
    # carry. That is allowed, but it must be visible rather than silent.
    unobserved = [f.instance_id for f in result.findings
                  if f.verdict == "FAIL" and not (f.observation or "").strip()]

    passed = not leaks and not prohibited
    bits = [f"{len(paragraphs)} paragraph(s) drafted"]
    if leaks:
        bits.append(f"{len(leaks)} internal term(s) found: {', '.join(leaks[:6])}")
    if prohibited:
        bits.append(f"{len(prohibited)} prohibited word(s): {', '.join(prohibited)}")
    if unobserved:
        bits.append(f"{len(unobserved)} matter(s) have no client-facing wording and are "
                    f"held back from the report")
    if passed:
        bits.append("no internal identifier or prohibited wording present")
    return Gate(passed=passed, summary="; ".join(bits), leaks=leaks,
                prohibited=prohibited, unobserved=unobserved)
