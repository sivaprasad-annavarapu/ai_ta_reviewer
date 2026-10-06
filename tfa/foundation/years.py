"""Assessment years, and the previous years behind them.

Everything in this system is indexed by assessment year, because that is how
both the return and the audit report identify themselves. The label is
normalised to `AY 2024-25` in one place so that "2024", "2024-25", "AY2024-25"
and "2024-2025" — all of which appear in real files — sort and join correctly
instead of becoming four different columns in the fact matrix.
"""
import re
from dataclasses import dataclass
from datetime import date
from typing import List, Optional

_PATTERNS = (
    re.compile(r"^\s*(?:AY|A\.?Y\.?)?\s*(\d{4})\s*[-/]\s*(\d{2,4})\s*$", re.I),
    re.compile(r"^\s*(?:AY|A\.?Y\.?)?\s*(\d{4})\s*$", re.I),
)


def normalise(value) -> Optional[str]:
    """`2024`, `2024-25`, `AY 2024-2025` -> `AY 2024-25`. None if unreadable.

    A four-digit year on its own is taken as the *start* of the assessment year,
    which is how both the ITR JSON (`AssessmentYear: "2024"`) and the 3CD schema
    (`AssessmentYear: "2024-25"`) mean it.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    for pattern in _PATTERNS:
        m = pattern.match(text)
        if not m:
            continue
        start = int(m.group(1))
        if not 1990 <= start <= 2100:
            return None
        return f"AY {start}-{str(start + 1)[-2:]}"
    return None


def start_year(ay_label: str) -> int:
    """`AY 2024-25` -> 2024. Raises on anything this module did not produce."""
    m = re.match(r"^AY (\d{4})-\d{2}$", ay_label)
    if not m:
        raise ValueError(f"not a normalised assessment year: {ay_label!r}")
    return int(m.group(1))


def previous_year(ay_label: str):
    """The financial year assessed. `AY 2024-25` -> 2023-04-01 .. 2024-03-31."""
    y = start_year(ay_label)
    return date(y - 1, 4, 1), date(y, 3, 31)


def fy_label(ay_label: str) -> str:
    y = start_year(ay_label)
    return f"FY {y - 1}-{str(y)[-2:]}"


def preceding(ay_label: str) -> str:
    y = start_year(ay_label)
    return f"AY {y - 1}-{str(y)[-2:]}"


def ordered(labels) -> List[str]:
    """Chronological, normalising on the way in and dropping anything that
    cannot be read as a year — the caller has already reported those as
    unreadable, and a bad label must not become a column of its own."""
    good = {normalise(l) for l in labels}
    good.discard(None)
    return sorted(good, key=start_year)


def span(labels) -> List[str]:
    """Every year between the earliest and the latest, including the ones with
    no filing. A gap is the point: it is what the attendance register reports."""
    known = ordered(labels)
    if not known:
        return []
    lo, hi = start_year(known[0]), start_year(known[-1])
    return [f"AY {y}-{str(y + 1)[-2:]}" for y in range(lo, hi + 1)]


@dataclass(frozen=True)
class Window:
    """The years an analysis covers: those present, plus the holes between."""
    years: List[str]                 # every year in the span, in order
    present: List[str]               # those with at least one filing
    missing: List[str]               # those in the span with none

    @property
    def label(self) -> str:
        if not self.years:
            return "no assessment year could be read"
        if len(self.years) == 1:
            return self.years[0]
        return f"{self.years[0]} to {self.years[-1]}"

    @property
    def count(self) -> int:
        return len(self.years)


def window(labels) -> Window:
    present = ordered(labels)
    full = span(present)
    return Window(years=full, present=present,
                  missing=[y for y in full if y not in present])
