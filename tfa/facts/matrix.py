"""The fact matrix: one canonical fact, one assessment year, one located value.

This is the only thing the rules see. They never touch the raw JSON, and that
is deliberate: a rule that reads the document directly can be broken by a
schema change in a way nobody notices, and a verdict that cannot name the cell
it came from cannot be re-derived. Everything downstream — trends, rules, model
judgment, the report — is a function of this table.

A cell that is absent is absent. There is no filling forward, no interpolation
and no zero. The absence of a number is itself one of the more informative
things this system reports.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..foundation import years as Y
from ..ingest.loader import FilingSet
from ..ingest.pointer import Located, as_number
from .extract import FactMap, FactSpec, evaluate, extract


@dataclass
class Cell:
    fact_id: str
    ay: str
    value: Any
    pointer: str = ""
    file: str = ""
    by: str = "path"                  # path | key search | computed
    detail: str = ""
    ref: str = ""                     # short citation handle, e.g. "C41"

    @property
    def number(self) -> Optional[float]:
        return as_number(self.value)

    @property
    def is_number(self) -> bool:
        return self.number is not None

    def render(self) -> str:
        where = f"{self.file}{self.pointer}" if self.file else self.pointer
        suffix = "" if self.by == "path" else f" [{self.by}]"
        return f"{self.ay} · {where}{suffix}"

    def as_dict(self) -> Dict:
        return {"fact": self.fact_id, "ay": self.ay, "value": self.value,
                "pointer": self.pointer, "file": self.file, "by": self.by,
                "detail": self.detail, "ref": self.ref}


@dataclass
class FactMatrix:
    years: List[str]
    fact_map: FactMap
    cells: Dict[Tuple[str, str], Cell] = field(default_factory=dict)
    sources: Dict[Tuple[str, str], str] = field(default_factory=dict)  # (ay, kind) -> file
    notes: List[str] = field(default_factory=list)

    # ------------------------------------------------------------- accessors
    def spec(self, fact_id: str) -> Optional[FactSpec]:
        return self.fact_map.by_id(fact_id)

    def get(self, fact_id: str, ay: str) -> Optional[Cell]:
        return self.cells.get((fact_id, ay))

    def num(self, fact_id: str, ay: str) -> Optional[float]:
        cell = self.get(fact_id, ay)
        return cell.number if cell else None

    def text(self, fact_id: str, ay: str) -> Optional[str]:
        cell = self.get(fact_id, ay)
        if not cell or cell.value in (None, ""):
            return None
        if isinstance(cell.value, list):
            return ", ".join(str(v) for v in cell.value)
        return str(cell.value)

    def flag(self, fact_id: str, ay: str) -> Optional[bool]:
        cell = self.get(fact_id, ay)
        if not cell:
            return None
        return cell.value if isinstance(cell.value, bool) else None

    def series(self, fact_id: str) -> Dict[str, float]:
        """Year -> number, for the years that have one. Ordered."""
        out: Dict[str, float] = {}
        for ay in self.years:
            value = self.num(fact_id, ay)
            if value is not None:
                out[ay] = value
        return out

    def available_years(self, fact_id: str) -> List[str]:
        return [ay for ay in self.years if (fact_id, ay) in self.cells]

    def pair(self, fact_id: str, ay: str) -> Tuple[Optional[float], Optional[float]]:
        """This year and the year before it, for continuity rules."""
        return self.num(fact_id, ay), self.num(fact_id, Y.preceding(ay))

    def has_source(self, ay: str, kind: str) -> bool:
        return (ay, kind) in self.sources

    def cells_for(self, ay: str) -> List[Cell]:
        return [c for (fid, year), c in self.cells.items() if year == ay]

    # -------------------------------------------------------------- summary
    def coverage(self) -> Dict:
        by_scope: Dict[str, Dict[str, int]] = {}
        for spec in self.fact_map.specs:
            row = by_scope.setdefault(spec.scope, {"declared": 0, "found": 0,
                                                   "by_search": 0})
            row["declared"] += 1
            for ay in self.years:
                cell = self.get(spec.id, ay)
                if cell:
                    row["found"] += 1
                    if cell.by == "key search":
                        row["by_search"] += 1
                    break
        return {
            "years": len(self.years),
            "facts_declared": len(self.fact_map.specs),
            "cells_possible": len(self.fact_map.specs) * max(len(self.years), 1),
            "cells_populated": len(self.cells),
            "found_by_search": sum(1 for c in self.cells.values()
                                   if c.by == "key search"),
            "computed": sum(1 for c in self.cells.values() if c.by == "computed"),
            "by_scope": by_scope,
        }

    def rows(self, only_populated: bool = True) -> List[Dict]:
        """The matrix as the screen shows it: one row per fact, one column per
        year, plus what is known about the fact itself."""
        out = []
        for spec in self.fact_map.specs:
            cells = {ay: self.get(spec.id, ay) for ay in self.years}
            populated = [ay for ay, c in cells.items() if c is not None]
            if only_populated and not populated:
                continue
            out.append({
                "id": spec.id, "label": spec.label, "group": spec.group,
                "group_label": self.fact_map.groups.get(spec.group, spec.group),
                "scope": spec.scope, "kind": spec.kind, "clause": spec.clause,
                "trend": spec.trend, "expr": spec.expr,
                "values": {ay: (c.value if c else None) for ay, c in cells.items()},
                "refs": {ay: (c.ref if c else None) for ay, c in cells.items()},
                "by": {ay: (c.by if c else None) for ay, c in cells.items()},
                "pointers": {ay: (f"{c.file}{c.pointer}" if c else None)
                             for ay, c in cells.items()},
                "years_present": len(populated),
            })
        return out


def build_matrix(filings: FilingSet, fact_map: FactMap) -> FactMatrix:
    years = filings.attendance.years
    matrix = FactMatrix(years=years, fact_map=fact_map)
    counter = 0

    for kind in ("3cd", "itr"):
        for ay, filing in filings.by_year(kind).items():
            matrix.sources[(ay, kind)] = filing.name
            for spec in fact_map.for_scope(kind):
                located: Optional[Located] = extract(spec, filing.doc, filing.name)
                if located is None:
                    continue
                counter += 1
                matrix.cells[(spec.id, ay)] = Cell(
                    fact_id=spec.id, ay=ay, value=located.value,
                    pointer=located.pointer, file=located.file, by=located.by,
                    detail=located.detail, ref=f"C{counter}")

    # ---- derived metrics, in declaration order ----------------------------
    for spec in fact_map.for_scope("derived"):
        for ay in years:
            inputs: Dict[str, float] = {}
            provenance: List[str] = []
            for name in _inputs(spec.expr):
                value = matrix.num(name, ay)
                if value is None:
                    inputs = {}
                    break
                inputs[name] = value
                cell = matrix.get(name, ay)
                if cell:
                    provenance.append(cell.ref)
            if not inputs:
                continue
            if spec.guard:
                guard = matrix.num(spec.guard, ay)
                if guard in (None, 0):
                    continue
            value = evaluate(spec.expr, inputs)
            if value is None:
                continue
            counter += 1
            matrix.cells[(spec.id, ay)] = Cell(
                fact_id=spec.id, ay=ay, value=round(value, 4), by="computed",
                detail=f"{spec.expr} — from {', '.join(provenance)}",
                ref=f"C{counter}")

    if not years:
        matrix.notes.append(
            "No assessment year could be read from any file, so no fact matrix was "
            "built. Nothing below this point in the analysis could run.")
    return matrix


def _inputs(expr: str) -> List[str]:
    import ast
    return sorted({n.id for n in ast.walk(ast.parse(expr, mode="eval"))
                   if isinstance(n, ast.Name)})
