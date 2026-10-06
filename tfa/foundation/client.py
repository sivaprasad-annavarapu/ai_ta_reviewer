"""The Client Context: what the firm declares about the assessee.

Declaring it is optional, and the difference matters. Where a context file
exists, the name, PAN, constitution and audit position in it are the firm's own
statement, and the filings are compared against it — a PAN in the return that
differs from the declared PAN is a finding. Where no context file exists, the
same fields are read out of the filings themselves and marked provisional, and
every comparison that would have tested the declaration is reported as not
performed rather than as passed. A check that cannot run must not look like a
check that ran.

    clients/ACME-PVT-LTD.yaml
"""
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .errors import ClientContextError
from .hashing import hash_obj

ENTITY_TYPES = {"company", "llp", "firm", "individual", "huf", "aop_boi", "trust"}
RUN_MODES = {"simulation", "production"}


@dataclass
class ClientContext:
    client_id: str
    name: Optional[str] = None
    pan: Optional[str] = None
    entity_type: Optional[str] = None
    nature_of_business: Optional[str] = None
    run_mode: str = "simulation"
    jurisdiction: str = "IN-INCOMETAX"
    filings_path: Optional[str] = None
    tax_audit_applicable: Optional[bool] = None
    first_year_of_business: Optional[str] = None
    engagement_partner: Optional[str] = None
    firm: Optional[str] = None
    attributes: Dict[str, Any] = field(default_factory=dict)
    declared: bool = True                 # False when read out of the filings
    source: str = ""                      # path, or how it was inferred
    notes: List[str] = field(default_factory=list)

    @property
    def digest(self) -> str:
        return hash_obj(asdict(self))

    @property
    def display_name(self) -> str:
        return self.name or self.client_id

    def attribute(self, name: str, default: Any = None) -> Any:
        if hasattr(self, name) and getattr(self, name) is not None:
            return getattr(self, name)
        return self.attributes.get(name, default)

    def manifest(self) -> Dict:
        out = asdict(self)
        out["digest"] = self.digest
        return out


def context_path(client_id: str, root: Path) -> Path:
    return root / "clients" / f"{client_id}.yaml"


def load_client(path: Path) -> ClientContext:
    raw = yaml.safe_load(path.read_text()) or {}
    if not raw.get("client_id"):
        raise ClientContextError(f"{path.name} declares no client_id")
    entity_type = raw.get("entity_type")
    if entity_type and entity_type not in ENTITY_TYPES:
        raise ClientContextError(
            f"{path.name} declares entity_type {entity_type!r}; expected one of "
            f"{sorted(ENTITY_TYPES)}")
    run_mode = raw.get("run_mode", "simulation")
    if run_mode not in RUN_MODES:
        raise ClientContextError(f"{path.name} declares unknown run_mode {run_mode!r}")
    return ClientContext(
        client_id=raw["client_id"], name=raw.get("name"),
        pan=(raw.get("pan") or None), entity_type=entity_type,
        nature_of_business=raw.get("nature_of_business"),
        run_mode=run_mode, jurisdiction=raw.get("jurisdiction", "IN-INCOMETAX"),
        filings_path=raw.get("filings_path"),
        tax_audit_applicable=raw.get("tax_audit_applicable"),
        first_year_of_business=raw.get("first_year_of_business"),
        engagement_partner=raw.get("engagement_partner"), firm=raw.get("firm"),
        attributes=raw.get("attributes", {}) or {},
        declared=True, source=str(path),
    )


def provisional_client(client_id: str) -> ClientContext:
    """No declaration on file. Identity will be filled in from the filings once
    they are read, and every declaration-versus-filing comparison is disabled
    for the run."""
    return ClientContext(
        client_id=client_id, declared=False,
        source="no context file — identity taken from the filings themselves",
        notes=["No client context was declared, so the identity below is what the "
               "filings say about themselves. Nothing in this run compares the "
               "filings against an independent declaration."],
    )
