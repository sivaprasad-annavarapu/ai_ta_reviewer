"""Loads the canonical rule catalogue and checks it against everything else.

Three kinds of defect in our own method are caught here, before a single filing
is opened:

  * a rule naming an implementation that does not exist;
  * a rule citing a provision the authority pack cannot resolve;
  * a rule whose parameters name facts the fact dictionary does not declare.

Each of those is a bug in the rule set, not a finding about a client, and the
difference matters enough to stop the run.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from ..facts.extract import FactMap
from ..foundation.authority import AuthorityPack
from ..foundation.errors import CitationError
from ..foundation.hashing import hash_file
from .library import REGISTRY

VALID_METHODS = {"rule", "llm", "hybrid"}
VALID_FOR_EACH = {"none", "year", "year_pair"}

# Parameters whose value is the id of a fact. Checked against the dictionary so
# that a renamed fact cannot leave a rule silently testing nothing.
FACT_PARAMS = ("fact", "against", "left", "right", "closing", "opening",
               "comparative", "original", "ratio", "numerator", "denominator",
               "flag", "amount", "total", "parts")


@dataclass(frozen=True)
class RuleSpec:
    id: str
    theme: str
    question: str
    method: str
    rule: str
    authority: List[str]
    severity: str
    for_each: str = "none"
    params: Dict[str, Any] = field(default_factory=dict)
    requires: List[str] = field(default_factory=list)
    applicability: List[str] = field(default_factory=list)
    rubric: str = ""
    scope: Dict[str, Any] = field(default_factory=dict)
    vulnerability: str = ""
    enquiry: str = ""

    @property
    def uses_model(self) -> bool:
        return self.method in ("llm", "hybrid")


@dataclass(frozen=True)
class RuleSet:
    version: str
    digest: str
    path: Path
    themes: Dict[str, str]
    rules: List[RuleSpec]

    def by_id(self, rule_id: str) -> RuleSpec:
        return next(r for r in self.rules if r.id == rule_id)

    def theme_label(self, theme_id: str) -> str:
        return self.themes.get(theme_id, theme_id)

    @property
    def counts(self) -> Dict[str, int]:
        return {
            "declared": len(self.rules),
            "deterministic": sum(1 for r in self.rules if r.method == "rule"),
            "judgment": sum(1 for r in self.rules if r.method == "llm"),
            "hybrid": sum(1 for r in self.rules if r.method == "hybrid"),
            "per_year": sum(1 for r in self.rules if r.for_each == "year"),
            "per_pair": sum(1 for r in self.rules if r.for_each == "year_pair"),
            "high": sum(1 for r in self.rules if r.severity == "high"),
        }


def load_rules(path: Path, pack: AuthorityPack,
               fact_map: Optional[FactMap] = None) -> RuleSet:
    raw = yaml.safe_load(path.read_text())
    themes = {t["id"]: t["label"] for t in raw.get("themes", [])}

    rules: List[RuleSpec] = []
    seen = set()
    for r in raw.get("rules", []):
        rid = r["id"]
        if rid in seen:
            raise CitationError(f"duplicate rule id {rid}")
        seen.add(rid)
        if r["theme"] not in themes:
            raise CitationError(f"rule {rid} names unknown theme {r['theme']!r}")
        method = r.get("method")
        if method not in VALID_METHODS:
            raise CitationError(f"rule {rid} has invalid method {method!r}")
        for_each = r.get("for_each", "none")
        if for_each not in VALID_FOR_EACH:
            raise CitationError(f"rule {rid} has unsupported for_each {for_each!r}")

        if method in ("rule", "hybrid"):
            if not r.get("rule"):
                raise CitationError(f"rule {rid} is method {method!r} but names no "
                                    f"implementation")
            if r["rule"] not in REGISTRY:
                raise CitationError(f"rule {rid} names unimplemented rule "
                                    f"{r['rule']!r}")
        if method in ("llm", "hybrid"):
            if not r.get("rubric"):
                raise CitationError(
                    f"rule {rid} puts a question to model judgment with no rubric — a "
                    f"question with no standard to answer against is how a model comes "
                    f"to invent one")
            if not r.get("authority"):
                raise CitationError(
                    f"rule {rid} puts a normative question to model judgment without "
                    f"citing a provision")
        if method == "llm" and r.get("rule"):
            raise CitationError(f"rule {rid} is method 'llm' but also names an "
                                f"implementation; use 'hybrid' if both are intended")

        for provision_id in r.get("authority", []):
            pack.resolve(provision_id)          # raises if it does not resolve

        params = r.get("params", {}) or {}
        if fact_map is not None:
            for key in FACT_PARAMS:
                value = params.get(key)
                if value and not fact_map.by_id(str(value)):
                    raise CitationError(
                        f"rule {rid} parameter {key}={value!r} is not a fact declared "
                        f"in the fact dictionary")

        rules.append(RuleSpec(
            id=rid, theme=r["theme"],
            question=" ".join(r["question"].split()),
            method=method, rule=r.get("rule", "") or "",
            authority=r.get("authority", []) or [],
            severity=r.get("severity", "medium"),
            for_each=for_each, params=params,
            requires=r.get("requires", []) or [],
            applicability=r.get("applicability", []) or [],
            rubric=" ".join((r.get("rubric") or "").split()),
            scope=r.get("scope", {}) or {},
            vulnerability=" ".join((r.get("vulnerability") or "").split()),
            enquiry=" ".join((r.get("enquiry") or "").split()),
        ))

    ids = {r.id for r in rules}
    for r in rules:
        unknown = [d for d in r.requires if d not in ids]
        if unknown:
            raise CitationError(f"rule {r.id} requires unknown rule(s) {unknown}")

    # Implementations that nothing calls are dead weight, and worth saying so.
    called = {r.rule for r in rules if r.rule}
    orphans = sorted(set(REGISTRY) - called)
    if orphans:
        import sys
        print(f"note: {len(orphans)} rule implementation(s) are not used by the "
              f"catalogue: {', '.join(orphans)}", file=sys.stderr)

    return RuleSet(version=str(raw["version"]), digest=hash_file(path), path=path,
                   themes=themes, rules=rules)


def default_rules_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "rules").glob("canonical-v*.yaml"))
    if not candidates:
        raise CitationError("no canonical rule catalogue found")
    return candidates[-1]
