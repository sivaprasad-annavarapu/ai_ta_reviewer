"""Loads and pins the authority pack.

Only the pack carries normative force. A citation that does not resolve here is
a defect in our own rule set, caught before a single filing is opened, rather
than a quiet fallback to whatever the model remembers about the section.

Provisions are filtered to the years under analysis: section 43B(h) did not
exist for AY 2021-22, and a rule that cites it must not fire on that year.
"""
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import yaml

from .errors import CitationError
from .hashing import hash_file
from . import years as Y


def _as_date(value) -> Optional[date]:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


@dataclass(frozen=True)
class Provision:
    id: str
    instrument: str
    reference: str
    title: str
    summary: str
    topics: List[str]
    effective_from: Optional[date]
    effective_to: Optional[date]
    verified: bool
    key_parameters: Dict = field(default_factory=dict)

    def in_force_for(self, ay_label: str) -> bool:
        """In force for an assessment year if its window overlaps that year's
        previous year — the year whose transactions the return reports."""
        start, end = Y.previous_year(ay_label)
        if self.effective_from and self.effective_from > end:
            return False
        if self.effective_to and self.effective_to < start:
            return False
        return True

    @property
    def citation(self) -> str:
        return self.reference


@dataclass(frozen=True)
class AuthorityPack:
    id: str
    name: str
    version: str
    verification_status: str
    path: Path
    digest: str
    provisions: Dict[str, Provision]
    entity_types: List[Dict]
    predicates: List[Dict]
    instruments: Dict[str, str]

    def resolve(self, provision_id: str) -> Provision:
        try:
            return self.provisions[provision_id]
        except KeyError:
            raise CitationError(
                f"citation {provision_id!r} does not resolve in pack "
                f"{self.id} v{self.version}")

    def in_force(self, ay_labels: Sequence[str]) -> Dict[str, Provision]:
        """A provision is in force for the run if it is in force for any year
        under analysis. Per-year force is re-tested where a rule needs it."""
        if not ay_labels:
            return dict(self.provisions)
        return {pid: p for pid, p in self.provisions.items()
                if any(p.in_force_for(ay) for ay in ay_labels)}

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "verified"

    @property
    def unverified_count(self) -> int:
        return sum(1 for p in self.provisions.values() if not p.verified)


def load_pack(path: Path) -> AuthorityPack:
    if not path.exists():
        raise CitationError(f"authority pack not found at {path}")
    raw = yaml.safe_load(path.read_text())

    provisions: Dict[str, Provision] = {}
    for p in raw.get("provisions", []):
        if p["id"] in provisions:
            raise CitationError(f"duplicate provision id {p['id']}")
        provisions[p["id"]] = Provision(
            id=p["id"], instrument=p["instrument"], reference=p["reference"],
            title=p["title"], summary=" ".join((p.get("summary") or "").split()),
            topics=p.get("topics", []) or [],
            effective_from=_as_date(p.get("effective_from")),
            effective_to=_as_date(p.get("effective_to")),
            verified=bool(p.get("verified", False)),
            key_parameters=p.get("key_parameters", {}) or {},
        )
    if not provisions:
        raise CitationError("authority pack contains no provisions")

    return AuthorityPack(
        id=raw["id"], name=raw["name"], version=str(raw["version"]),
        verification_status=raw.get("verification_status", "unverified_draft"),
        path=path, digest=hash_file(path), provisions=provisions,
        entity_types=raw.get("entity_types", []) or [],
        predicates=raw.get("applicability_predicates", []) or [],
        instruments={i["id"]: i["name"] for i in raw.get("instruments", [])},
    )


def default_pack_path(pack_id: str = "IN-INCOMETAX",
                      root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge" / "authority").glob(f"{pack_id}-v*.yaml"))
    if not candidates:
        raise CitationError(f"no authority pack found for {pack_id!r}")
    return candidates[-1]
