"""What a rule may see, and what it must give back.

A rule reads the fact matrix, the trends and the attendance register. It does
not read the JSON, and it does not read the client's mind: everything it
concludes is a function of located cells, and every cell it relied on comes
back with the verdict so a reader can go and look.

A finding also carries an *enquiry* — what to ask, or where to look next. An
anomaly with no suggested next step is a complaint; with one, it is a working
paper.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..facts.matrix import Cell, FactMatrix

PASS = "PASS"
FAIL = "FAIL"
NOT_APPLICABLE = "NOT_APPLICABLE"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
VERDICTS = (PASS, FAIL, NOT_APPLICABLE, INSUFFICIENT_EVIDENCE)


@dataclass
class RuleResult:
    verdict: str
    statement: str                                   # internal wording
    evidence: List[Cell] = field(default_factory=list)
    confidence: float = 0.95
    detail: Dict[str, Any] = field(default_factory=dict)
    na_reason: str = ""
    observation: str = ""                            # wording safe to release
    enquiry: str = ""                                # what to ask or look at next
    amount: Optional[float] = None                   # what is at stake, in rupees

    @property
    def failed(self) -> bool:
        return self.verdict == FAIL


def ok(statement: str, *evidence: Optional[Cell], **kw) -> RuleResult:
    return RuleResult(PASS, statement, [c for c in evidence if c], **kw)


def bad(statement: str, *evidence: Optional[Cell], **kw) -> RuleResult:
    return RuleResult(FAIL, statement, [c for c in evidence if c], **kw)


def unknown(statement: str, *evidence: Optional[Cell], **kw) -> RuleResult:
    kw.setdefault("confidence", 0.5)
    return RuleResult(INSUFFICIENT_EVIDENCE, statement, [c for c in evidence if c], **kw)


def not_applicable(reason: str, **kw) -> RuleResult:
    return RuleResult(NOT_APPLICABLE, f"Not applicable: {reason}", confidence=1.0,
                      na_reason=reason, **kw)


@dataclass
class RuleContext:
    foundation: Any                     # foundation.preflight.Foundation
    matrix: FactMatrix
    trends: Dict[str, Any]
    filings: Any                        # ingest.loader.FilingSet
    ay: Optional[str] = None            # the year this instance is about
    prior_ay: Optional[str] = None      # the year before it, where there is one
    params: Dict[str, Any] = field(default_factory=dict)

    # -------------------------------------------------------------- doctrine
    def param(self, name: str, default: Any) -> Any:
        if name in self.params:
            return self.params[name]
        return self.foundation.doctrine.param(name, default)

    @property
    def floor(self) -> float:
        return float(self.param("materiality_floor_inr", 50000))

    @property
    def tolerance(self) -> float:
        return float(self.param("tie_out_tolerance_inr", 1))

    # ----------------------------------------------------------------- facts
    def cell(self, fact_id: str, ay: Optional[str] = None) -> Optional[Cell]:
        return self.matrix.get(fact_id, ay or self.ay)

    def num(self, fact_id: str, ay: Optional[str] = None) -> Optional[float]:
        return self.matrix.num(fact_id, ay or self.ay)

    def text(self, fact_id: str, ay: Optional[str] = None) -> Optional[str]:
        return self.matrix.text(fact_id, ay or self.ay)

    def flag(self, fact_id: str, ay: Optional[str] = None) -> Optional[bool]:
        return self.matrix.flag(fact_id, ay or self.ay)

    def series(self, fact_id: str) -> Dict[str, float]:
        return self.matrix.series(fact_id)

    def trend(self, fact_id: str):
        return self.trends.get(fact_id)

    def label(self, fact_id: str) -> str:
        spec = self.matrix.spec(fact_id)
        return spec.label if spec else fact_id

    # ------------------------------------------------------------ comparison
    def agrees(self, a: float, b: float) -> bool:
        """Two figures for the same thing. Absolute tolerance for rupees, plus a
        relative one so that a crore reported to the nearest thousand is not
        called a discrepancy."""
        gap = abs(a - b)
        if gap <= self.tolerance:
            return True
        scale = max(abs(a), abs(b))
        return bool(scale) and gap / scale <= float(
            self.param("tie_out_tolerance_relative", 0.001))

    def material(self, amount: Optional[float]) -> bool:
        return amount is not None and abs(amount) >= self.floor

    def significant(self, fact_id: str, gap: Optional[float],
                    scale: float = 0.0) -> bool:
        """Is a difference worth reporting?

        For money, the answer is the rupee floor from the doctrine: forty per
        cent of nine hundred rupees is noise. For a quantity or a count the
        floor is meaningless — thirteen hundred units of stock is not "below
        fifty thousand" — so a proportion of the larger side is used instead.
        """
        if gap is None:
            return False
        spec = self.matrix.spec(fact_id)
        if spec is not None and spec.kind == "money":
            return abs(gap) >= self.floor
        relative = float(self.param("non_money_material_fraction", 0.005))
        if scale:
            return abs(gap) / scale >= relative
        return abs(gap) > 0

    def has_both_documents(self, ay: Optional[str] = None) -> bool:
        year = ay or self.ay
        return (self.matrix.has_source(year, "itr")
                and self.matrix.has_source(year, "3cd"))

    def source(self, kind: str, ay: Optional[str] = None) -> Optional[str]:
        return self.matrix.sources.get((ay or self.ay, kind))


def money(value: Optional[float]) -> str:
    """Indian grouping, because the reader of this is reading a return."""
    if value is None:
        return "not available"
    negative = value < 0
    whole = f"{abs(int(round(value))):d}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if negative else "") + whole


def pct(value: Optional[float], places: int = 1) -> str:
    if value is None:
        return "not available"
    return f"{value * 100:.{places}f}%"
