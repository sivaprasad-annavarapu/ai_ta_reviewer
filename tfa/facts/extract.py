"""Turning filings into located facts.

The rule for this module is that it never guesses and never rounds a gap to
zero. Three outcomes only: a value with a pointer to where it was read, a value
with a pointer and a note that it was found by searching rather than at a known
path, or nothing at all with the reason recorded.

`aggregate: sum` over a wildcard path is the one place where several located
values become one fact — the depreciation allowable across every block, the
43B liability across every head. The pointers of every contributing value are
kept, because a total that cannot be broken back down is not evidence.
"""
import ast
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from ..foundation.errors import FactMapError
from ..foundation.hashing import hash_file
from ..ingest.pointer import Located, as_number, find_key, resolve_path

TRUTHY = {"y", "yes", "true", "1"}
FALSY = {"n", "no", "false", "0"}
VALID_KINDS = {"money", "ratio", "count", "date", "text", "flag", "list"}
VALID_AGG = {"first", "sum", "max", "min", "count", "list", "any_true"}


@dataclass(frozen=True)
class FactSpec:
    id: str
    label: str
    group: str
    scope: str                       # itr | 3cd | derived
    kind: str
    paths: List[str] = field(default_factory=list)
    keys: List[str] = field(default_factory=list)
    within: str = ""
    aggregate: str = "first"
    clause: str = ""
    trend: bool = False
    expr: str = ""
    guard: str = ""
    note: str = ""

    @property
    def numeric(self) -> bool:
        return self.kind in ("money", "ratio", "count")


@dataclass
class FactMap:
    version: str
    path: Path
    digest: str
    groups: Dict[str, str]
    specs: List[FactSpec]

    def by_id(self, fact_id: str) -> Optional[FactSpec]:
        return next((s for s in self.specs if s.id == fact_id), None)

    def for_scope(self, scope: str) -> List[FactSpec]:
        return [s for s in self.specs if s.scope == scope]


def load_fact_map(path: Optional[Path] = None) -> FactMap:
    path = path or default_fact_map_path()
    raw = yaml.safe_load(path.read_text())
    groups = {g["id"]: g["label"] for g in raw.get("groups", [])}
    specs: List[FactSpec] = []
    seen = set()

    def add(entry: Dict, scope: str) -> None:
        fid = entry["id"]
        if fid in seen:
            raise FactMapError(f"duplicate fact id {fid!r}")
        seen.add(fid)
        if entry.get("group") not in groups:
            raise FactMapError(f"fact {fid!r} names unknown group {entry.get('group')!r}")
        kind = entry.get("kind", "money")
        if kind not in VALID_KINDS:
            raise FactMapError(f"fact {fid!r} has unknown kind {kind!r}")
        agg = entry.get("aggregate", "first")
        if agg not in VALID_AGG:
            raise FactMapError(f"fact {fid!r} has unknown aggregate {agg!r}")
        if scope in ("itr", "3cd") and not (entry.get("paths") or entry.get("keys")):
            raise FactMapError(f"fact {fid!r} gives neither a path nor a key to look for")
        specs.append(FactSpec(
            id=fid, label=entry["label"], group=entry["group"], scope=scope,
            kind=kind, paths=list(entry.get("paths") or []),
            keys=list(entry.get("keys") or []), within=entry.get("within", "") or "",
            aggregate=agg, clause=str(entry.get("clause", "") or ""),
            trend=bool(entry.get("trend", False)),
            expr=entry.get("expr", "") or "", guard=entry.get("guard", "") or "",
            note=entry.get("note", "") or "",
        ))

    for entry in raw.get("facts", []):
        scope = entry.get("scope")
        if scope not in ("itr", "3cd"):
            raise FactMapError(f"fact {entry.get('id')!r} has scope {scope!r}; "
                               f"expected 'itr' or '3cd'")
        add(entry, scope)
    for entry in raw.get("derived", []):
        if not entry.get("expr"):
            raise FactMapError(f"derived metric {entry.get('id')!r} has no expression")
        add(entry, "derived")

    # A derived metric may only refer to facts that exist, and never to a later
    # derived metric — the order in the file is the order of computation.
    known = set()
    for spec in specs:
        if spec.scope != "derived":
            known.add(spec.id)
    for spec in specs:
        if spec.scope != "derived":
            continue
        for name in _names(spec.expr):
            if name not in known:
                raise FactMapError(
                    f"derived metric {spec.id!r} refers to {name!r}, which is not a "
                    f"fact declared before it")
        if spec.guard and spec.guard not in known:
            raise FactMapError(f"derived metric {spec.id!r} guards on unknown "
                               f"fact {spec.guard!r}")
        known.add(spec.id)

    return FactMap(version=str(raw["version"]), path=path, digest=hash_file(path),
                   groups=groups, specs=specs)


def default_fact_map_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "facts").glob("fact-map-v*.yaml"))
    if not candidates:
        raise FactMapError("no fact map found")
    return candidates[-1]


# --------------------------------------------------------------- expressions
_BINOPS = {ast.Add: operator.add, ast.Sub: operator.sub,
           ast.Mult: operator.mul, ast.Div: operator.truediv}


def _names(expr: str) -> List[str]:
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise FactMapError(f"expression {expr!r} does not parse: {exc.msg}")
    return sorted({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)})


def evaluate(expr: str, values: Dict[str, float]) -> Optional[float]:
    """Arithmetic over fact values. Any missing input makes the whole metric
    unavailable, which is the correct answer: a ratio computed from three of
    its four inputs is not a smaller ratio, it is a wrong one."""
    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            left, right = walk(node.left), walk(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Div) and right == 0:
                return None
            return _BINOPS[type(node.op)](left, right)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inner = walk(node.operand)
            return None if inner is None else (inner if isinstance(node.op, ast.UAdd)
                                               else -inner)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            return values.get(node.id)
        raise FactMapError(f"expression uses something that is not arithmetic: "
                           f"{ast.dump(node)[:60]}")
    return walk(ast.parse(expr, mode="eval"))


# ----------------------------------------------------------------- extraction
def as_flag(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in TRUTHY:
        return True
    if text in FALSY:
        return False
    return None


def extract(spec: FactSpec, doc: Any, file_name: str) -> Optional[Located]:
    """One fact out of one document, or None."""
    hits: List[tuple] = []
    by = "path"
    for path in spec.paths:
        hits = resolve_path(doc, path)
        hits = [(p, v) for p, v in hits if v not in (None, "", [], {})]
        if hits:
            break
    if not hits and spec.keys:
        by = "key search"
        hits = find_key(doc, spec.keys, numeric_only=spec.numeric,
                        within=spec.within or None, limit=32)
        hits = [(p, v) for p, v in hits if v not in (None, "", [], {})]
    if not hits:
        return None

    agg = spec.aggregate
    if agg == "first":
        pointer, value = hits[0]
        detail = (f"{len(hits)} values matched; the first was taken"
                  if len(hits) > 1 else "")
        return Located(value=_coerce(spec, value), pointer=pointer, by=by,
                       file=file_name, detail=detail)

    if agg in ("sum", "max", "min"):
        numbers = [(p, as_number(v)) for p, v in hits]
        numbers = [(p, n) for p, n in numbers if n is not None]
        if not numbers:
            return None
        fn = {"sum": sum, "max": max, "min": min}[agg]
        total = fn(n for _, n in numbers) if agg == "sum" else fn(n for _, n in numbers)
        first = numbers[0][0]
        return Located(
            value=total, pointer=first, by=by, file=file_name,
            detail=(f"{agg} of {len(numbers)} value(s): "
                    + ", ".join(p for p, _ in numbers[:8])
                    + (" …" if len(numbers) > 8 else "")))

    if agg == "count":
        return Located(value=len(hits), pointer=hits[0][0], by=by, file=file_name,
                       detail=f"{len(hits)} entries")

    if agg == "list":
        values = [str(v).strip() for _, v in hits if str(v).strip()]
        return Located(value=values, pointer=hits[0][0], by=by, file=file_name,
                       detail=f"{len(values)} entries")

    if agg == "any_true":
        flags = [as_flag(v) for _, v in hits]
        return Located(value=any(f for f in flags if f is not None),
                       pointer=hits[0][0], by=by, file=file_name,
                       detail=f"{len(flags)} flags")
    return None


NAME_ORDER = ("FirstName", "MiddleName", "SurNameOrOrgName", "LastName",
              "OrgName", "NameOfAssessee")


def stitch(value: Dict) -> str:
    """A name arrives as one string in some forms and as a first/middle/last
    object in others — and in a real filing often as `LastName` alone carrying
    the whole firm name. One fact must hold one name, or the years cannot be
    compared."""
    parts = [str(value[k]).strip() for k in NAME_ORDER
             if isinstance(value.get(k), str) and value.get(k).strip()]
    if not parts:
        parts = [str(v).strip() for v in value.values()
                 if isinstance(v, str) and v.strip()]
    return " ".join(" ".join(parts).split())


def _coerce(spec: FactSpec, value: Any) -> Any:
    if spec.kind == "flag":
        flag = as_flag(value)
        return value if flag is None else flag
    if spec.numeric:
        number = as_number(value)
        return value if number is None else number
    if spec.kind == "text" and isinstance(value, dict):
        return stitch(value) or value
    if spec.kind == "list" and isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items() if not isinstance(v, (dict, list))]
    if spec.kind == "list" and not isinstance(value, list):
        return [str(value)]
    return value
