"""The audit programme, assembled from what this analysis actually found.

The last course of the sandwich, and deterministic again. Nothing here calls a
model: the inference layer has already named the areas it thinks matter, the
rules have already failed where they failed, and the trends have already
crossed the thresholds they crossed. This module does the arithmetic of putting
those three together and printing a programme a person can work through.

Two properties make it worth reading rather than skimming:

  * every area on the plan says what put it there — the rule, the movement or
    the inference — so a partner can strike an area out on the merits rather
    than on suspicion of padding;
  * every area *not* on the plan is listed too, with the reason. A programme
    that shows only what it selected cannot be reviewed for what it missed.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from ..foundation.hashing import hash_file
from ..rules.context import money

# What each kind of trigger is worth. Declared here because a weighting nobody
# can see is a judgment nobody agreed to.
WEIGHT = {
    "rule_high": 40, "rule_medium": 18, "rule_low": 8,
    "inference_high": 30, "inference_medium": 14,
    "movement": 6, "always": 4,
}
PRIORITY_BANDS = ((60, "high"), (24, "medium"), (0, "low"))


@dataclass(frozen=True)
class Procedure:
    id: str
    text: str
    authority: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class AreaSpec:
    id: str
    label: str
    why: str
    order: int
    procedures: List[Procedure]
    rules: List[str] = field(default_factory=list)
    themes: List[str] = field(default_factory=list)
    facts: List[str] = field(default_factory=list)
    inferences: List[str] = field(default_factory=list)
    always: bool = False


@dataclass
class ProgrammeCatalogue:
    version: str
    path: Path
    digest: str
    areas: List[AreaSpec]

    def by_id(self, area_id: str) -> Optional[AreaSpec]:
        return next((a for a in self.areas if a.id == area_id), None)


def load_programme(path: Optional[Path] = None, pack=None) -> ProgrammeCatalogue:
    path = path or default_programme_path()
    raw = yaml.safe_load(path.read_text())
    areas: List[AreaSpec] = []
    for a in raw.get("areas", []):
        procedures = []
        for p in a.get("procedures", []) or []:
            for provision in p.get("authority", []) or []:
                if pack is not None:
                    pack.resolve(provision)          # a procedure cannot cite fiction
            procedures.append(Procedure(id=p["id"],
                                        text=" ".join(p["text"].split()),
                                        authority=list(p.get("authority", []) or [])))
        triggers = a.get("triggers", {}) or {}
        areas.append(AreaSpec(
            id=a["id"], label=a["label"], why=" ".join(a["why"].split()),
            order=int(a.get("order", 99)), procedures=procedures,
            rules=list(triggers.get("rules", []) or []),
            themes=list(triggers.get("themes", []) or []),
            facts=list(triggers.get("facts", []) or []),
            inferences=list(triggers.get("inferences", []) or []),
            always=bool(triggers.get("always", False))))
    areas.sort(key=lambda a: a.order)
    return ProgrammeCatalogue(version=str(raw["version"]), path=path,
                              digest=hash_file(path), areas=areas)


def default_programme_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "procedures").glob("canonical-v*.yaml"))
    if not candidates:
        raise FileNotFoundError("no audit programme catalogue found")
    return candidates[-1]


@dataclass
class PlannedArea:
    spec: AreaSpec
    score: int
    priority: str
    reasons: List[Dict]
    years: List[str]
    amount: float
    procedures: List[Dict]

    def as_dict(self) -> Dict:
        return {
            "id": self.spec.id, "label": self.spec.label, "why": self.spec.why,
            "priority": self.priority, "score": self.score,
            "reasons": self.reasons, "years": self.years, "amount": self.amount,
            "procedures": self.procedures,
        }


@dataclass
class Programme:
    areas: List[PlannedArea]
    omitted: List[Dict]
    version: str
    digest: str
    note: str = ""

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "areas": len(self.areas),
            "high": sum(1 for a in self.areas if a.priority == "high"),
            "medium": sum(1 for a in self.areas if a.priority == "medium"),
            "low": sum(1 for a in self.areas if a.priority == "low"),
            "procedures": sum(len(a.procedures) for a in self.areas),
            "omitted": len(self.omitted),
        }

    @property
    def amount(self) -> float:
        return sum(a.amount for a in self.areas)

    def as_dict(self) -> Dict:
        return {"areas": [a.as_dict() for a in self.areas], "omitted": self.omitted,
                "counts": self.counts, "amount": self.amount,
                "version": self.version, "digest": self.digest, "note": self.note}


def _priority(score: int) -> str:
    for floor, label in PRIORITY_BANDS:
        if score >= floor:
            return label
    return "low"


def build_programme(findings: Sequence, inferences: Sequence, trends: Dict,
                    catalogue: ProgrammeCatalogue,
                    material: float = 0.30) -> Programme:
    raised = [f for f in findings if f.verdict == "FAIL"]
    reportable = [i for i in inferences if getattr(i, "reportable", False)]

    planned: List[PlannedArea] = []
    omitted: List[Dict] = []

    for spec in catalogue.areas:
        score = 0
        reasons: List[Dict] = []
        years: List[str] = []
        amount = 0.0

        # ---- what the rules found -----------------------------------------
        for f in raised:
            if f.rule_id in spec.rules or f.theme in spec.themes:
                score += WEIGHT.get(f"rule_{f.severity}", WEIGHT["rule_low"])
                amount += f.amount or 0
                if f.ay and f.ay not in years:
                    years.append(f.ay)
                reasons.append({
                    "kind": "rule", "ref": f.instance_id, "severity": f.severity,
                    "ay": f.ay, "amount": f.amount,
                    "text": f.observation or f.statement[:220]})

        # ---- what the inference layer named -------------------------------
        for i in reportable:
            if i.spec_id in spec.inferences:
                score += WEIGHT.get(f"inference_{i.severity}",
                                    WEIGHT["inference_medium"])
                reasons.append({
                    "kind": "inference", "ref": i.spec_id, "severity": i.severity,
                    "ay": None, "amount": None,
                    "text": i.headline or i.title})
                # The work the inference implies comes from its hypotheses: each
                # one names what would confirm it and the record that would
                # show it. That is a procedure, written by the layer that knows
                # why it is needed, and assembled here rather than there.
                for h in i.hypotheses[:3]:
                    test = (h.get("confirms_if") or "").strip()
                    document = (h.get("document") or "").strip()
                    if not test and not document:
                        continue
                    procedure = ""
                    if document:
                        procedure = (f"Obtain {document[0].lower() + document[1:]}"
                                     if document[0].isupper() else
                                     f"Obtain {document}")
                        procedure = procedure.rstrip(".") + "."
                    if test:
                        procedure += f" It confirms or refutes: {test.rstrip('.')}."
                    reasons.append({
                        "kind": "hypothesis", "ref": i.spec_id,
                        "severity": i.severity, "ay": None, "amount": None,
                        "text": (h.get("explanation") or "")[:300],
                        "procedure": procedure.strip()})
                for focus in i.audit_focus:
                    reasons.append({
                        "kind": "focus", "ref": i.spec_id, "severity": i.severity,
                        "ay": None, "amount": None,
                        "text": f"{focus.get('area', '')}: {focus.get('why', '')}",
                        "procedure": focus.get("procedure", "")})

        # ---- what merely moved --------------------------------------------
        for fact in spec.facts:
            trend = trends.get(fact)
            if trend is None:
                continue
            biggest = trend.largest_step
            if biggest and biggest.pct is not None and abs(biggest.pct) >= material:
                score += WEIGHT["movement"]
                reasons.append({
                    "kind": "movement", "ref": fact, "severity": "low",
                    "ay": biggest.ay, "amount": biggest.change,
                    "text": f"{trend.label} moves {biggest.pct * 100:+.0f}% in "
                            f"{biggest.ay}"})
                break                   # one movement per area is enough to indicate it

        if spec.always:
            score += WEIGHT["always"]
            reasons.append({"kind": "standing", "ref": spec.id, "severity": "low",
                            "ay": None, "amount": None,
                            "text": "part of every programme for these years"})

        if not reasons or (score <= WEIGHT["always"] and not spec.always):
            omitted.append({
                "id": spec.id, "label": spec.label,
                "reason": "nothing in this analysis indicated it: no rule failed on "
                          "it, no series moved materially, and no inference named it"})
            continue

        procedures = [{"id": p.id, "text": p.text, "authority": p.authority}
                      for p in spec.procedures]
        # A procedure the inference layer asked for, which the library does not
        # carry, is added and marked as such: the library is meant to grow.
        for r in reasons:
            extra = (r.get("procedure") or "").strip()
            if extra and not any(extra.lower() in p["text"].lower()
                                 for p in procedures):
                procedures.append({"id": f"{spec.id}-AD", "text": extra,
                                   "authority": [], "from_inference": r["ref"]})

        planned.append(PlannedArea(
            spec=spec, score=score, priority=_priority(score), reasons=reasons,
            years=sorted(years), amount=amount, procedures=procedures))

    planned.sort(key=lambda a: (-a.score, a.spec.order))
    note = ("Areas are ordered by what this analysis found, not by convention. Every "
            "area names what put it on the plan, and the areas nothing indicated are "
            "listed below it with the reason.")
    return Programme(areas=planned, omitted=omitted, version=catalogue.version,
                     digest=catalogue.digest, note=note)
