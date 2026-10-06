"""Charts, drawn by Python from the fact matrix.

Three rules hold this module together.

**A chart is a declared view, not a rendering decision.** Which series, in what
unit, with what note, comes from the catalogue. Nothing here chooses what to
show; it only draws what was declared, and reports the parts of a declaration
that had no data.

**Every point carries its cell.** A chart is evidence, so each plotted value
keeps the fact, the year, the file and the JSON pointer it was read from. A
picture a reader cannot get behind is worse than a table.

**The model never sees the picture.** The inference layer is handed the same
series as a written brief. That is what makes an inference checkable against
the chart a person is looking at, rather than an opinion about an image.

The SVG is written by hand rather than by a library: it is a few hundred lines
of geometry, it themes with the surrounding page through CSS variables, and it
prints. A charting dependency would decide the house style on our behalf.
"""
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import yaml

from ..facts.matrix import Cell, FactMatrix
from ..foundation import years as Y
from ..foundation.errors import FactMapError
from ..foundation.hashing import hash_file
from ..rules.context import money

VALID_KINDS = {"line", "bars", "grouped", "ratio", "stacked"}
VALID_UNITS = {"inr", "pct", "days", "count"}

# Series colours. Declared here rather than in the catalogue: a chart says what
# it shows, and the house decides how it looks.
PALETTE = ("var(--accent)", "var(--pos)", "var(--neg)", "var(--warn)",
           "#8b5cf6", "#0e7490")


@dataclass(frozen=True)
class SeriesSpec:
    fact: str
    label: str
    unit: str = ""


@dataclass(frozen=True)
class ChartSpec:
    id: str
    title: str
    group: str
    kind: str
    unit: str
    series: List[SeriesSpec]
    overlay: List[SeriesSpec] = field(default_factory=list)
    note: str = ""
    report: bool = False

    @property
    def facts(self) -> List[str]:
        return [s.fact for s in self.series] + [s.fact for s in self.overlay]


@dataclass
class ChartCatalogue:
    version: str
    path: Path
    digest: str
    groups: Dict[str, str]
    charts: List[ChartSpec]

    def by_id(self, chart_id: str) -> Optional[ChartSpec]:
        return next((c for c in self.charts if c.id == chart_id), None)

    @property
    def in_report(self) -> List[ChartSpec]:
        return [c for c in self.charts if c.report]


def load_charts(path: Optional[Path] = None,
                fact_map=None) -> ChartCatalogue:
    path = path or default_charts_path()
    raw = yaml.safe_load(path.read_text())
    groups = {g["id"]: g["label"] for g in raw.get("groups", [])}
    charts: List[ChartSpec] = []
    seen = set()
    for c in raw.get("charts", []):
        cid = c["id"]
        if cid in seen:
            raise FactMapError(f"duplicate chart id {cid}")
        seen.add(cid)
        if c["kind"] not in VALID_KINDS:
            raise FactMapError(f"chart {cid} has unknown kind {c['kind']!r}")
        if c["unit"] not in VALID_UNITS:
            raise FactMapError(f"chart {cid} has unknown unit {c['unit']!r}")
        if c.get("group") not in groups:
            raise FactMapError(f"chart {cid} names unknown group {c.get('group')!r}")

        def read(entries) -> List[SeriesSpec]:
            out = []
            for s in entries or []:
                if fact_map is not None and not fact_map.by_id(s["fact"]):
                    raise FactMapError(
                        f"chart {cid} plots {s['fact']!r}, which is not a fact in the "
                        f"dictionary")
                out.append(SeriesSpec(fact=s["fact"], label=s["label"],
                                      unit=s.get("unit", "")))
            return out

        charts.append(ChartSpec(
            id=cid, title=c["title"], group=c["group"], kind=c["kind"],
            unit=c["unit"], series=read(c.get("series")),
            overlay=read(c.get("overlay")),
            note=" ".join((c.get("note") or "").split()),
            report=bool(c.get("report", False))))
    return ChartCatalogue(version=str(raw["version"]), path=path,
                          digest=hash_file(path), groups=groups, charts=charts)


def default_charts_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "charts").glob("canonical-v*.yaml"))
    if not candidates:
        raise FactMapError("no chart catalogue found")
    return candidates[-1]


# ------------------------------------------------------------------- drawing
@dataclass
class Point:
    ay: str
    value: Optional[float]
    ref: str = ""
    pointer: str = ""
    file: str = ""

    @property
    def present(self) -> bool:
        return self.value is not None


@dataclass
class Series:
    fact: str
    label: str
    unit: str
    points: List[Point]
    colour: str = ""

    @property
    def values(self) -> Dict[str, float]:
        return {p.ay: p.value for p in self.points if p.present}

    @property
    def present(self) -> int:
        return sum(1 for p in self.points if p.present)


@dataclass
class Chart:
    spec: ChartSpec
    years: List[str]
    series: List[Series]
    overlay: List[Series]
    missing: List[str] = field(default_factory=list)

    @property
    def drawable(self) -> bool:
        """Two points somewhere, or there is no line to draw and no comparison
        to make. A chart with one column is a number, and a number belongs in a
        table."""
        return sum(s.present for s in self.series + self.overlay) >= 2

    def as_dict(self) -> Dict:
        return {
            "id": self.spec.id, "title": self.spec.title, "group": self.spec.group,
            "kind": self.spec.kind, "unit": self.spec.unit, "note": self.spec.note,
            "report": self.spec.report, "years": self.years,
            "drawable": self.drawable, "missing": self.missing,
            "series": [{"fact": s.fact, "label": s.label, "unit": s.unit,
                        "colour": s.colour,
                        "points": [{"ay": p.ay, "value": p.value, "ref": p.ref,
                                    "pointer": (f"{p.file}{p.pointer}"
                                                if p.file else p.pointer)}
                                   for p in s.points]}
                       for s in self.series],
            "overlay": [{"fact": s.fact, "label": s.label, "unit": s.unit,
                         "colour": s.colour,
                         "points": [{"ay": p.ay, "value": p.value, "ref": p.ref}
                                    for p in s.points]}
                        for s in self.overlay],
            "svg": render(self),
        }


def build(matrix: FactMatrix, catalogue: ChartCatalogue) -> List[Chart]:
    charts: List[Chart] = []
    for spec in catalogue.charts:
        missing: List[str] = []

        def series_for(entries, offset: int = 0) -> List[Series]:
            out = []
            for i, s in enumerate(entries):
                points = []
                for ay in matrix.years:
                    cell: Optional[Cell] = matrix.get(s.fact, ay)
                    points.append(Point(
                        ay=ay,
                        value=(cell.number if cell else None),
                        ref=(cell.ref if cell else ""),
                        pointer=(cell.pointer if cell else ""),
                        file=(cell.file if cell else "")))
                if not any(p.present for p in points):
                    missing.append(s.fact)
                    continue
                out.append(Series(fact=s.fact, label=s.label,
                                  unit=(s.unit or spec.unit), points=points,
                                  colour=PALETTE[(i + offset) % len(PALETTE)]))
            return out

        drawn = series_for(spec.series)
        over = series_for(spec.overlay, offset=len(spec.series))
        charts.append(Chart(spec=spec, years=list(matrix.years), series=drawn,
                            overlay=over, missing=missing))
    return charts


# --------------------------------------------------------------------- SVG
W, H = 720, 260
PAD = {"l": 76, "r": 62, "t": 16, "b": 34}
PLOT_W = W - PAD["l"] - PAD["r"]
PLOT_H = H - PAD["t"] - PAD["b"]


def _fmt(value: Optional[float], unit: str) -> str:
    if value is None:
        return "—"
    if unit == "pct":
        return f"{value:.1f}%"
    if unit == "days":
        return f"{value:.0f}d"
    if unit == "count":
        return f"{value:,.0f}"
    return _compact(value)


def _compact(value: float) -> str:
    """Rupees, at the scale a reader of Indian accounts thinks in."""
    a = abs(value)
    if a >= 1e7:
        return f"{value / 1e7:,.2f} cr"
    if a >= 1e5:
        return f"{value / 1e5:,.2f} L"
    return money(value)


def _bounds(series: Sequence[Series], zero_based: bool) -> Tuple[float, float]:
    values = [p.value for s in series for p in s.points if p.present]
    if not values:
        return 0.0, 1.0
    lo, hi = min(values), max(values)
    if zero_based:
        lo = min(0.0, lo)
    if hi == lo:
        hi = lo + (abs(lo) or 1.0)
    span = hi - lo
    return lo - span * 0.08, hi + span * 0.10


def _ticks(lo: float, hi: float, count: int = 4) -> List[float]:
    """Round numbers a reader recognises, rather than the data's own extremes."""
    span = hi - lo
    if span <= 0:
        return [lo]
    raw = span / count
    magnitude = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    for factor in (1, 2, 2.5, 5, 10):
        step = magnitude * factor
        if step >= raw:
            break
    start = math.ceil(lo / step) * step
    out = []
    value = start
    while value <= hi + step * 0.001 and len(out) < 12:
        out.append(round(value, 6))
        value += step
    return out


def render(chart: Chart) -> str:
    """One self-contained SVG. Colours are CSS variables, so it themes with the
    page it is dropped into and needs no second version for dark mode."""
    if not chart.drawable:
        return ""
    years = chart.years
    if not years:
        return ""

    kind = chart.spec.kind
    zero_based = kind in ("bars", "grouped", "stacked") or chart.spec.unit == "pct"
    scale_series = chart.series if kind != "stacked" else []
    if kind == "stacked":
        totals = []
        for i, ay in enumerate(years):
            total = sum((s.points[i].value or 0) for s in chart.series)
            totals.append(Point(ay=ay, value=total))
        scale_series = [Series(fact="_total", label="total", unit=chart.spec.unit,
                               points=totals)]
    lo, hi = _bounds(scale_series or chart.series, zero_based)

    def x(index: int) -> float:
        if len(years) == 1:
            return PAD["l"] + PLOT_W / 2
        return PAD["l"] + index * PLOT_W / (len(years) - 1)

    def band(index: int) -> Tuple[float, float]:
        width = PLOT_W / max(len(years), 1)
        return PAD["l"] + index * width, width

    def y(value: float) -> float:
        return PAD["t"] + PLOT_H - (value - lo) / (hi - lo) * PLOT_H

    parts: List[str] = [
        f'<svg class="cht" viewBox="0 0 {W} {H}" width="100%" '
        f'preserveAspectRatio="xMidYMid meet" role="img" '
        f'aria-label="{_esc(chart.spec.title)}">',
        '<g class="grid">',
    ]

    # ---- grid and the left axis -------------------------------------------
    for tick in _ticks(lo, hi):
        ty = y(tick)
        if not (PAD["t"] - 1 <= ty <= PAD["t"] + PLOT_H + 1):
            continue
        parts.append(f'<line x1="{PAD["l"]}" y1="{ty:.1f}" x2="{PAD["l"] + PLOT_W}" '
                     f'y2="{ty:.1f}"/>')
        parts.append(f'<text class="ax" x="{PAD["l"] - 8}" y="{ty + 3.5:.1f}" '
                     f'text-anchor="end">{_esc(_fmt(tick, chart.spec.unit))}</text>')
    parts.append("</g>")

    # ---- the years --------------------------------------------------------
    for i, ay in enumerate(years):
        cx = x(i) if kind in ("line", "ratio") else band(i)[0] + band(i)[1] / 2
        parts.append(f'<text class="ax" x="{cx:.1f}" y="{H - 12}" '
                     f'text-anchor="middle">{_esc(ay.replace("AY ", ""))}</text>')

    # ---- the series -------------------------------------------------------
    if kind in ("line", "ratio"):
        for s in chart.series:
            parts.extend(_line(s, x, y))
    elif kind == "bars":
        parts.extend(_bars(chart.series, band, y, lo))
    elif kind == "grouped":
        parts.extend(_grouped(chart.series, band, y, lo))
    elif kind == "stacked":
        parts.extend(_stacked(chart.series, band, y, lo))

    # ---- the overlay, on its own right-hand scale -------------------------
    if chart.overlay:
        olo, ohi = _bounds(chart.overlay, zero_based=True)

        def oy(value: float) -> float:
            return PAD["t"] + PLOT_H - (value - olo) / (ohi - olo) * PLOT_H

        for s in chart.overlay:
            parts.extend(_line(s, x, oy, dashed=True))
        for tick in _ticks(olo, ohi, 3):
            ty = oy(tick)
            if not (PAD["t"] - 1 <= ty <= PAD["t"] + PLOT_H + 1):
                continue
            parts.append(f'<text class="ax over" x="{PAD["l"] + PLOT_W + 8}" '
                         f'y="{ty + 3.5:.1f}">'
                         f'{_esc(_fmt(tick, chart.overlay[0].unit))}</text>')

    parts.append("</svg>")
    return "".join(parts)


def _line(s: Series, x, y, dashed: bool = False) -> List[str]:
    out: List[str] = []
    run: List[str] = []
    dash = ' stroke-dasharray="4 3"' if dashed else ""
    for i, p in enumerate(s.points):
        if not p.present:
            # A gap is a gap: the line breaks rather than joining across a year
            # nobody filed.
            if len(run) > 1:
                out.append(f'<polyline class="ln" points="{" ".join(run)}" '
                           f'style="stroke:{s.colour}"{dash}/>')
            run = []
            continue
        run.append(f"{x(i):.1f},{y(p.value):.1f}")
    if len(run) > 1:
        out.append(f'<polyline class="ln" points="{" ".join(run)}" '
                   f'style="stroke:{s.colour}"{dash}/>')
    for i, p in enumerate(s.points):
        if p.present:
            out.append(f'<circle class="pt" cx="{x(i):.1f}" cy="{y(p.value):.1f}" '
                       f'r="2.6" style="fill:{s.colour}">'
                       f'<title>{_esc(s.label)} · {_esc(p.ay)} · '
                       f'{_esc(_fmt(p.value, s.unit))}</title></circle>')
    return out


def _bars(series: Sequence[Series], band, y, lo: float) -> List[str]:
    out: List[str] = []
    count = max(len(series), 1)
    for si, s in enumerate(series):
        for i, p in enumerate(s.points):
            if not p.present or p.value == 0:
                continue
            left, width = band(i)
            inner = width * 0.72 / count
            bx = left + width * 0.14 + si * inner
            top = y(max(p.value, 0))
            base = y(max(lo, 0))
            out.append(f'<rect class="br" x="{bx:.1f}" y="{top:.1f}" '
                       f'width="{inner * 0.86:.1f}" '
                       f'height="{max(abs(base - top), 1):.1f}" '
                       f'style="fill:{s.colour}">'
                       f'<title>{_esc(s.label)} · {_esc(p.ay)} · '
                       f'{_esc(_fmt(p.value, s.unit))}</title></rect>')
    return out


def _grouped(series: Sequence[Series], band, y, lo: float) -> List[str]:
    return _bars(series, band, y, lo)


def _stacked(series: Sequence[Series], band, y, lo: float) -> List[str]:
    out: List[str] = []
    years = len(series[0].points) if series else 0
    for i in range(years):
        left, width = band(i)
        bx = left + width * 0.2
        bw = width * 0.6
        running = 0.0
        for s in series:
            p = s.points[i]
            if not p.present or p.value == 0:
                continue
            top = y(running + p.value)
            bottom = y(running)
            out.append(f'<rect class="br" x="{bx:.1f}" y="{top:.1f}" '
                       f'width="{bw:.1f}" height="{max(bottom - top, 0.6):.1f}" '
                       f'style="fill:{s.colour}">'
                       f'<title>{_esc(s.label)} · {_esc(p.ay)} · '
                       f'{_esc(_fmt(p.value, s.unit))}</title></rect>')
            running += p.value
    return out


def _esc(text: Any) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))
