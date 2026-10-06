"""Locating a value inside a filing, and being able to say where it came from.

Every fact this system extracts carries a JSON Pointer (RFC 6901) back to the
exact place in the exact file it was read from. That is what makes a finding
checkable by a person who does not trust it: they can open the file at that
pointer and see the number for themselves.

The lookup deliberately has three levels, tried in order:

    1. an explicit path, wildcards allowed — used where we know the schema;
    2. a search for a named leaf key anywhere in the document — used because
       real ITR JSON moves fields between schedules from one year to the next,
       and a rigid path silently produces "not available" when the number is
       right there;
    3. nothing, reported as nothing.

Level 2 is a compromise and is recorded as one: a fact resolved by search
carries `by="key search"` so a reviewer can see which numbers were found the
careful way and which were found the willing way.
"""
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

SCALAR = (str, int, float, bool)


def escape(token: str) -> str:
    return str(token).replace("~", "~0").replace("/", "~1")


def unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def join(*tokens) -> str:
    return "".join("/" + escape(t) for t in tokens)


def walk(node: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    """Every scalar in the document, with its pointer. Depth-first, in document
    order, so two runs over the same file visit values in the same sequence."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, prefix + "/" + escape(key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from walk(value, prefix + "/" + str(i))
    else:
        yield prefix, node


def get(doc: Any, pointer: str) -> Any:
    """Resolve a JSON Pointer. Returns None for anything that does not resolve —
    a missing field and a field containing null are the same absence here, and
    both are reported as unavailable rather than as zero."""
    if pointer in ("", "/"):
        return doc
    node = doc
    for raw in pointer.lstrip("/").split("/"):
        token = unescape(raw)
        if isinstance(node, dict):
            if token not in node:
                return None
            node = node[token]
        elif isinstance(node, list):
            try:
                node = node[int(token)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return node


def resolve_path(doc: Any, path: str, max_descent: int = 6) -> List[Tuple[str, Any]]:
    """A dotted path with two wildcards.

        `*`   any single key or index at this level
        `**`  any number of levels, including none

        **.Form3cdDeprAllw.*.DepAllowable

    `**` exists because the same form arrives wrapped differently by different
    software: one utility writes {"FORM3CA": {"F3CA": …}} and another writes
    {"metadata": …, "data": {"FORM3CB": {"F3CB": …}}}. A path that has to know
    which is a path that silently reports "not available" for half the files it
    is given. Descent is bounded so a deep document cannot turn one lookup into
    a walk of the whole tree.

    Returns every match with its concrete pointer, so a wildcard that hits four
    array elements gives four located values rather than one arbitrary one.
    """
    results: List[Tuple[str, Any]] = []
    seen: set = set()

    def step(node: Any, parts: Sequence[str], pointer: str) -> None:
        if not parts:
            if pointer not in seen:
                seen.add(pointer)
                results.append((pointer, node))
            return
        head, rest = parts[0], parts[1:]

        if head == "**":
            step(node, rest, pointer)                     # match nothing
            def descend(current: Any, current_pointer: str, depth: int) -> None:
                if depth > max_descent:
                    return
                if isinstance(current, dict):
                    for k, v in current.items():
                        child = current_pointer + "/" + escape(k)
                        step(v, rest, child)
                        descend(v, child, depth + 1)
                elif isinstance(current, list):
                    for i, v in enumerate(current):
                        child = current_pointer + "/" + str(i)
                        step(v, rest, child)
                        descend(v, child, depth + 1)
            descend(node, pointer, 1)
            return

        if head == "*":
            if isinstance(node, dict):
                for k, v in node.items():
                    step(v, rest, pointer + "/" + escape(k))
            elif isinstance(node, list):
                for i, v in enumerate(node):
                    step(v, rest, pointer + "/" + str(i))
            return

        if isinstance(node, dict) and head in node:
            step(node[head], rest, pointer + "/" + escape(head))
        elif isinstance(node, list) and head.isdigit() and int(head) < len(node):
            step(node[int(head)], rest, pointer + "/" + head)

    step(doc, [p for p in path.split(".") if p], "")
    return results


def find_key(doc: Any, names: Sequence[str], *, numeric_only: bool = False,
             within: Optional[str] = None, limit: int = 24) -> List[Tuple[str, Any]]:
    """Every leaf whose key matches one of `names`, case- and separator-blind.

    `within` restricts the search to pointers containing that fragment, which is
    how a fact says "the TotalIncome inside PartB_TI, not the one inside a
    schedule that happens to use the same word".
    """
    wanted = {_key(n) for n in names}
    out: List[Tuple[str, Any]] = []

    def visit(node: Any, pointer: str, key: Optional[str]) -> None:
        if len(out) >= limit:
            return
        if isinstance(node, dict):
            for k, v in node.items():
                visit(v, pointer + "/" + escape(k), k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                visit(v, pointer + "/" + str(i), key)
        else:
            if key is None or _key(key) not in wanted:
                return
            if within and within.lower() not in pointer.lower():
                return
            if numeric_only and as_number(node) is None:
                return
            out.append((pointer, node))

    visit(doc, "", None)
    return out


def _key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


_NUM = re.compile(r"^-?\d{1,15}(\.\d+)?$")


def as_number(value: Any) -> Optional[float]:
    """A number, or None. Strings are accepted because return JSON carries
    numbers as strings in places, but a string that is not a number is not
    coerced to zero — it is not a number, and saying so is the point."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if _NUM.match(text):
            return float(text)
    return None


@dataclass(frozen=True)
class Located:
    """A value and where it was found. `by` records how hard we had to look."""
    value: Any
    pointer: str
    by: str                       # "path" | "key search" | "computed" | "declared"
    file: str = ""
    detail: str = ""

    @property
    def number(self) -> Optional[float]:
        return as_number(self.value)

    def render(self) -> str:
        where = f"{self.file}{self.pointer}" if self.file else self.pointer
        return where if self.by == "path" else f"{where} [{self.by}]"
