"""Content addressing.

Every input to a run is hashed so the run can be re-derived. A digest is
recorded with the algorithm in front of it, because a bare hex string outlives
the memory of what produced it.
"""
import hashlib
import json
from pathlib import Path
from typing import Any


def hash_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def hash_text(text: str) -> str:
    return hash_bytes(text.encode("utf-8"))


def hash_file(path: Path) -> str:
    return hash_bytes(Path(path).read_bytes())


def hash_obj(obj: Any) -> str:
    """Stable across runs: keys sorted, non-JSON values coerced to their string
    form rather than raising."""
    return hash_text(json.dumps(obj, sort_keys=True, default=str))
