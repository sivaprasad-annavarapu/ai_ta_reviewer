"""Reading a client folder, and keeping an honest register of what was in it.

Two things matter here beyond parsing JSON.

First, coverage. A file that could not be read is recorded as a file that could
not be read, and every later conclusion of the form "this was never reported"
is qualified by it. Absence of evidence becomes evidence of absence only when
the folder was read completely.

Second, attendance. The point of a multi-year analysis is the years that are
missing and the pairs that are half present — a return with no audit report, an
audit report with no return. The register is built before any rule runs and is
shown before any finding, because it is the frame every finding sits in.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from ..foundation import years as Y
from ..foundation.hashing import hash_bytes
from .detect import Detection, detect, size_of

SKIP_NAMES = ("~$", ".")
JSON_SUFFIXES = {".json", ".jsn"}


@dataclass
class Filing:
    path: Path
    name: str
    rel: str
    digest: str
    bytes: int
    readable: bool
    doc: Any = None
    detection: Optional[Detection] = None
    error: str = ""
    folder_hint: Optional[str] = None      # assessment year implied by the folder
    stats: Dict[str, int] = field(default_factory=dict)
    ms: float = 0.0
    superseded_by: Optional[str] = None    # rel path of the filing that replaces it

    @property
    def kind(self) -> str:
        return self.detection.kind if self.detection else "unreadable"

    @property
    def ay(self) -> Optional[str]:
        return self.detection.ay if self.detection else None

    @property
    def form(self) -> str:
        return self.detection.form if self.detection else ""

    @property
    def in_play(self) -> bool:
        return self.readable and self.kind in ("itr", "3cd") and not self.superseded_by

    def as_dict(self) -> Dict:
        d = self.detection
        return {
            "file": self.name, "rel": self.rel, "bytes": self.bytes,
            "digest": self.digest, "readable": self.readable, "error": self.error,
            "kind": self.kind, "form": self.form, "ay": self.ay,
            "ay_raw": (d.ay_raw if d else None),
            "pan": (d.pan if d else None), "name": (d.name if d else None),
            "folder_hint": self.folder_hint,
            "populated": self.stats.get("populated", 0),
            "leaves": self.stats.get("leaves", 0),
            "notes": (d.notes if d else []),
            "superseded_by": self.superseded_by,
            "ms": self.ms,
        }


@dataclass
class Attendance:
    """Which of the two documents is present for each year in the span."""
    years: List[str]
    rows: List[Dict]
    present_years: List[str]
    missing_years: List[str]
    unreadable: List[Dict]
    unrecognised: List[Dict]
    duplicates: List[Dict]
    year_unknown: List[Dict]

    @property
    def complete(self) -> bool:
        """Both documents present for every year in the span, nothing unread."""
        return (not self.unreadable and not self.unrecognised and not self.year_unknown
                and all(r["itr"] and r["3cd"] for r in self.rows))

    @property
    def absence_safe(self) -> bool:
        """Can this record support a statement that something was never filed?

        Only if nothing that could have been a filing went unread. A text note
        in the folder is reported by the coverage rule, but it cannot be the
        return nobody can find, and letting it suppress that finding would be a
        different kind of dishonesty from the one the guard exists to prevent.
        """
        unread_json = [u for u in self.unreadable
                       if "not a JSON file" not in u.get("reason", "")]
        return not unread_json and not self.unrecognised and not self.year_unknown

    @property
    def statement(self) -> str:
        if not self.rows:
            return "No filing in the folder could be identified as a return or an audit report"
        pairs = sum(1 for r in self.rows if r["itr"] and r["3cd"])
        bits = [f"{len(self.years)} assessment year{'s' if len(self.years) != 1 else ''} "
                f"in the span, {pairs} with both a return and an audit report"]
        halves = [r["ay"] for r in self.rows if bool(r["itr"]) != bool(r["3cd"])]
        if halves:
            bits.append(f"{len(halves)} with only one of the two ({', '.join(halves)})")
        if self.missing_years:
            bits.append(f"{len(self.missing_years)} with neither "
                        f"({', '.join(self.missing_years)})")
        if self.unreadable:
            bits.append(f"{len(self.unreadable)} file(s) could not be read")
        if self.unrecognised:
            bits.append(f"{len(self.unrecognised)} file(s) were not a return or an "
                        f"audit report")
        return "; ".join(bits)

    def as_dict(self) -> Dict:
        return {
            "years": self.years, "rows": self.rows,
            "present_years": self.present_years, "missing_years": self.missing_years,
            "unreadable": self.unreadable, "unrecognised": self.unrecognised,
            "duplicates": self.duplicates, "year_unknown": self.year_unknown,
            "complete": self.complete, "absence_safe": self.absence_safe,
            "statement": self.statement,
        }


@dataclass
class FilingSet:
    root: Path
    filings: List[Filing]
    attendance: Attendance

    def by_year(self, kind: str) -> Dict[str, Filing]:
        out: Dict[str, Filing] = {}
        for f in self.filings:
            if f.in_play and f.kind == kind and f.ay:
                out.setdefault(f.ay, f)
        return out

    @property
    def readable(self) -> List[Filing]:
        return [f for f in self.filings if f.in_play]

    def by_rel(self, rel: str) -> Optional[Filing]:
        return next((f for f in self.filings if f.rel == rel), None)


def _folder_hint(path: Path, root: Path) -> Optional[str]:
    for part in path.relative_to(root).parts[:-1]:
        hint = Y.normalise(part.replace("_", "-"))
        if hint:
            return hint
    return Y.normalise(path.stem)


def load_filings(root: Path, on_file: Optional[Callable[[Dict], None]] = None) -> FilingSet:
    root = Path(root)
    filings: List[Filing] = []

    candidates = sorted(
        p for p in root.rglob("*")
        if p.is_file() and not p.name.startswith(SKIP_NAMES)
    )

    for path in candidates:
        started = time.perf_counter()
        rel = str(path.relative_to(root))
        raw = b""
        try:
            raw = path.read_bytes()
        except OSError as exc:
            filings.append(Filing(path, path.name, rel, "", 0, False,
                                  error=f"could not be opened ({exc.strerror})"))
            continue

        filing = Filing(path=path, name=path.name, rel=rel, digest=hash_bytes(raw),
                        bytes=len(raw), readable=False,
                        folder_hint=_folder_hint(path, root))

        if path.suffix.lower() not in JSON_SUFFIXES:
            filing.error = (f"not a JSON file ({path.suffix or 'no extension'}); this "
                            f"analysis reads the JSON produced by the e-filing utility")
        else:
            try:
                filing.doc = json.loads(raw.decode("utf-8-sig"))
                filing.readable = True
            except UnicodeDecodeError:
                filing.error = "not valid UTF-8 text"
            except json.JSONDecodeError as exc:
                filing.error = f"not valid JSON — {exc.msg} at line {exc.lineno}"

        if filing.readable:
            filing.detection = detect(filing.doc)
            filing.stats = size_of(filing.doc)

        filing.ms = round((time.perf_counter() - started) * 1000, 1)
        filings.append(filing)
        if on_file:
            on_file(filing.as_dict())

    _mark_superseded(filings)
    return FilingSet(root=root, filings=filings, attendance=_attendance(filings))


def _mark_superseded(filings: List[Filing]) -> None:
    """Two returns for one year is the normal shape of a revised filing, not an
    error. The later one governs; the earlier is kept, marked, and excluded from
    the fact matrix so that one year does not silently hold two turnovers.

    Later is decided by the creation date inside the file where there is one,
    and by file modification time where there is not — never by file name.
    """
    from .pointer import find_key

    groups: Dict[tuple, List[Filing]] = {}
    for f in filings:
        if f.in_play and f.ay:
            groups.setdefault((f.ay, f.kind), []).append(f)

    for (_ay, _kind), group in groups.items():
        if len(group) < 2:
            continue

        def stamp(f: Filing):
            for names in (["JSONCreationDate", "CreationDate", "Date_of_filing",
                           "FilingDate", "AckDate"],):
                for _, value in find_key(f.doc, names, limit=3):
                    text = str(value)[:10]
                    if len(text) == 10 and text[4] in "-/":
                        return text.replace("/", "-")
            return ""

        ordered = sorted(group, key=lambda f: (stamp(f), f.path.stat().st_mtime))
        winner = ordered[-1]
        for loser in ordered[:-1]:
            loser.superseded_by = winner.rel


def _attendance(filings: List[Filing]) -> Attendance:
    labels = [f.ay for f in filings if f.in_play and f.ay]
    win = Y.window(labels)

    by_year: Dict[str, Dict[str, List[Filing]]] = {
        y: {"itr": [], "3cd": []} for y in win.years}
    for f in filings:
        if f.in_play and f.ay in by_year:
            by_year[f.ay][f.kind].append(f)

    rows = []
    for year in win.years:
        itr = by_year[year]["itr"]
        tcd = by_year[year]["3cd"]
        rows.append({
            "ay": year, "fy": Y.fy_label(year),
            "itr": (itr[0].as_dict() if itr else None),
            "3cd": (tcd[0].as_dict() if tcd else None),
            "state": ("both" if itr and tcd else
                      "return only" if itr else
                      "audit report only" if tcd else "neither"),
        })

    duplicates = [{"ay": f.ay, "kind": f.kind, "file": f.name,
                   "superseded_by": f.superseded_by}
                  for f in filings if f.superseded_by]

    return Attendance(
        years=win.years, rows=rows,
        present_years=win.present, missing_years=win.missing,
        unreadable=[{"file": f.name, "reason": f.error}
                    for f in filings if not f.readable],
        unrecognised=[{"file": f.name, "reason": "not recognised as an income-tax "
                                                 "return or a tax audit report"}
                      for f in filings if f.readable and f.kind == "unknown"],
        duplicates=duplicates,
        year_unknown=[{"file": f.name, "kind": f.kind,
                       "reason": "no assessment year could be read from the file"}
                      for f in filings if f.readable and f.kind != "unknown" and not f.ay],
    )
