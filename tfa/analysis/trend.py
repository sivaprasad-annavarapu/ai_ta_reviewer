"""What the numbers did over the years.

Measurement only. Nothing here decides that anything is wrong — a series that
triples is a fact about the series, and whether that matters is a question for
a rule with a threshold behind it and a provision behind that. Keeping the two
apart is what stops the analysis from degenerating into "this number is big".

Every statistic is computed on the years that actually have a value. A gap is
skipped, never interpolated, and the gap is reported alongside the statistic so
a reader knows a five-year trend was drawn through three points.
"""
from dataclasses import dataclass, field
from statistics import mean, pstdev
from typing import Dict, List, Optional

from ..foundation import years as Y


@dataclass
class Step:
    """One year against the one before it."""
    ay: str
    previous_ay: str
    value: float
    previous: float

    @property
    def change(self) -> float:
        return self.value - self.previous

    @property
    def pct(self) -> Optional[float]:
        if self.previous == 0:
            return None
        return (self.value - self.previous) / abs(self.previous)

    def as_dict(self) -> Dict:
        return {"ay": self.ay, "previous_ay": self.previous_ay,
                "value": self.value, "previous": self.previous,
                "change": self.change,
                "pct": (round(self.pct, 4) if self.pct is not None else None)}


@dataclass
class Trend:
    fact_id: str
    label: str
    group: str
    kind: str                      # money | ratio | count
    values: Dict[str, float]
    steps: List[Step] = field(default_factory=list)
    gaps: List[str] = field(default_factory=list)

    # ------------------------------------------------------------ statistics
    @property
    def observations(self) -> int:
        return len(self.values)

    @property
    def first(self) -> Optional[float]:
        return next(iter(self.values.values()), None)

    @property
    def latest(self) -> Optional[float]:
        return list(self.values.values())[-1] if self.values else None

    @property
    def latest_year(self) -> Optional[str]:
        return list(self.values)[-1] if self.values else None

    @property
    def total_change(self) -> Optional[float]:
        if self.observations < 2:
            return None
        return self.latest - self.first

    @property
    def cagr(self) -> Optional[float]:
        """Only where the series starts positive and spans at least two years.
        A growth rate through a loss is arithmetic that means nothing."""
        if self.observations < 2:
            return None
        first, last = self.first, self.latest
        if first is None or last is None or first <= 0 or last <= 0:
            return None
        periods = Y.start_year(list(self.values)[-1]) - Y.start_year(list(self.values)[0])
        if periods <= 0:
            return None
        return (last / first) ** (1 / periods) - 1

    @property
    def mean(self) -> Optional[float]:
        return mean(self.values.values()) if self.values else None

    @property
    def volatility(self) -> Optional[float]:
        """Standard deviation of the year-on-year percentage changes."""
        pcts = [s.pct for s in self.steps if s.pct is not None]
        return pstdev(pcts) if len(pcts) >= 2 else None

    def z_of_latest(self) -> Optional[float]:
        """How far the last year sits from the mean of the years before it."""
        if self.observations < 3:
            return None
        earlier = list(self.values.values())[:-1]
        spread = pstdev(earlier)
        if not spread:
            return None
        return (self.latest - mean(earlier)) / spread

    @property
    def largest_step(self) -> Optional[Step]:
        with_pct = [s for s in self.steps if s.pct is not None]
        if not with_pct:
            return None
        return max(with_pct, key=lambda s: abs(s.pct))

    @property
    def monotonic(self) -> str:
        if self.observations < 3:
            return "too short to say"
        changes = [s.change for s in self.steps]
        if all(c > 0 for c in changes):
            return "rising every year"
        if all(c < 0 for c in changes):
            return "falling every year"
        return "mixed"

    def direction(self, material: float) -> str:
        """A single word for the shape of the series, against a declared
        threshold for what counts as a movement rather than a wobble."""
        if self.observations < 2:
            return "single year"
        biggest = self.largest_step
        if biggest and abs(biggest.pct or 0) >= material * 2.5:
            return "step change"
        volatility = self.volatility
        if volatility is not None and volatility >= material:
            return "volatile"
        total = self.total_change or 0
        base = abs(self.first or 0)
        if base and abs(total) / base >= material:
            return "rising" if total > 0 else "falling"
        return "broadly flat"

    def as_dict(self, material: float) -> Dict:
        biggest = self.largest_step
        return {
            "fact": self.fact_id, "label": self.label, "group": self.group,
            "kind": self.kind,
            "values": self.values, "observations": self.observations,
            "gaps": self.gaps,
            "first": self.first, "latest": self.latest,
            "latest_year": self.latest_year,
            "total_change": self.total_change,
            "cagr": (round(self.cagr, 4) if self.cagr is not None else None),
            "mean": self.mean,
            "volatility": (round(self.volatility, 4)
                           if self.volatility is not None else None),
            "z_latest": (round(self.z_of_latest(), 2)
                         if self.z_of_latest() is not None else None),
            "monotonic": self.monotonic,
            "direction": self.direction(material),
            "largest_step": (biggest.as_dict() if biggest else None),
            "steps": [s.as_dict() for s in self.steps],
        }


def build_trends(matrix, material: float = 0.30) -> Dict[str, Trend]:
    """One trend per numeric fact declared as worth trending."""
    trends: Dict[str, Trend] = {}
    for spec in matrix.fact_map.specs:
        if not spec.trend or spec.kind not in ("money", "ratio", "count"):
            continue
        values = matrix.series(spec.id)
        if not values:
            continue
        trend = Trend(fact_id=spec.id, label=spec.label, group=spec.group,
                      kind=spec.kind, values=values,
                      gaps=[ay for ay in matrix.years if ay not in values])
        ordered = list(values)
        for i in range(1, len(ordered)):
            trend.steps.append(Step(ay=ordered[i], previous_ay=ordered[i - 1],
                                    value=values[ordered[i]],
                                    previous=values[ordered[i - 1]]))
        trends[spec.id] = trend
    return trends


def summarise(trends: Dict[str, Trend], material: float) -> Dict:
    """Counts for the header, and the movements worth a reader's attention."""
    shapes: Dict[str, int] = {}
    for t in trends.values():
        shapes[t.direction(material)] = shapes.get(t.direction(material), 0) + 1
    movements = []
    for t in trends.values():
        biggest = t.largest_step
        if biggest and biggest.pct is not None and abs(biggest.pct) >= material:
            movements.append({"fact": t.fact_id, "label": t.label,
                              "kind": t.kind, **biggest.as_dict()})
    movements.sort(key=lambda m: abs(m["pct"]), reverse=True)
    return {"series": len(trends), "shapes": shapes,
            "material_movements": movements[:40],
            "material_threshold": material}
