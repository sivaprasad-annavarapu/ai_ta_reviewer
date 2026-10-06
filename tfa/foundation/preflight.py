"""What is fixed before a single filing is opened.

The client context, the authority pack, the doctrine and the rule catalogue are
loaded and pinned first. If our own method is broken — a rule citing a section
the pack does not carry — the run stops here, with an error about us, rather
than proceeding to say something wrong about a client.

Two things cannot be settled this early, because they depend on which years
turned up: which provisions are in force, and which applicability predicates
hold. `settle()` fixes those once the filings have been read, and records that
it did.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .authority import AuthorityPack, default_pack_path, load_pack
from .client import ClientContext, context_path, load_client, provisional_client
from .doctrine import Doctrine, load_doctrine
from .errors import ClientContextError
from .hashing import hash_obj

ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Foundation:
    client: ClientContext
    pack: AuthorityPack
    doctrine: Doctrine
    filings_root: Path
    warnings: List[str] = field(default_factory=list)
    provisions_in_force: Dict[str, Any] = field(default_factory=dict)
    predicates: Dict[str, bool] = field(default_factory=dict)
    years: List[str] = field(default_factory=list)
    settled: bool = False

    # ------------------------------------------------------------------ later
    def settle(self, years: List[str], observed: Optional[Dict[str, bool]] = None) -> None:
        """Fix the year-dependent parts, once the filings have said which years
        exist and what they contain."""
        observed = observed or {}
        self.years = list(years)
        self.provisions_in_force = self.pack.in_force(years)
        entity = (self.client.entity_type or "").lower()
        self.predicates = {
            "is_company": entity == "company",
            "is_firm_or_llp": entity in ("firm", "llp"),
            "has_partners": entity in ("firm", "llp", "aop_boi"),
            "mat_applicable": entity == "company"
                              and not observed.get("concessional_regime", False),
            "is_tax_audit_case": bool(
                self.client.tax_audit_applicable
                if self.client.tax_audit_applicable is not None
                else observed.get("audit_report_present", False)),
            "presumptive_claimed": bool(observed.get("presumptive_claimed", False)),
            "multi_year": len(years) >= 2,
        }
        if not entity:
            self.warnings.append(
                "The constitution of the assessee is not declared and could not be read "
                "from the filings, so rules that apply only to a company, or only to a "
                "firm, were not run.")
        self.settled = True

    def manifest(self) -> Dict:
        m = {
            "client": self.client.manifest(),
            "authority_pack": {
                "id": self.pack.id, "version": self.pack.version,
                "status": self.pack.verification_status,
                "digest": self.pack.digest,
                "provisions": len(self.pack.provisions),
                "provisions_unverified": self.pack.unverified_count,
                "provisions_in_force": len(self.provisions_in_force),
            },
            "doctrine": self.doctrine.manifest(),
            "years_analysed": list(self.years),
            "predicates": dict(self.predicates),
            "warnings": list(self.warnings),
        }
        m["foundation_digest"] = hash_obj(m)
        return m


def preflight(client_id: str, root: Optional[Path] = None,
              filings_root: Optional[Path] = None) -> Foundation:
    root = root or ROOT
    warnings: List[str] = []

    path = context_path(client_id, root)
    if path.is_file():
        client = load_client(path)
        if client.client_id != client_id:
            raise ClientContextError(
                f"{path.name} declares client_id {client.client_id!r}, which is not the "
                f"folder it was loaded for ({client_id!r})")
    else:
        client = provisional_client(client_id)
        warnings.append(
            f"No client context at clients/{client_id}.yaml. The identity of the "
            f"assessee is taken from the filings, and nothing in this run tests the "
            f"filings against an independent declaration.")

    pack = load_pack(default_pack_path(client.jurisdiction))
    doctrine = load_doctrine()

    if client.run_mode == "production" and not pack.is_verified:
        raise ClientContextError(
            f"run_mode is 'production' but authority pack {pack.id} v{pack.version} is "
            f"{pack.verification_status}. A production run against unverified law is "
            f"refused; verify the pack or run in simulation.")
    if not pack.is_verified:
        warnings.append(
            f"Authority pack {pack.id} v{pack.version} is {pack.verification_status}: "
            f"{pack.unverified_count} of {len(pack.provisions)} provisions have not been "
            f"checked against the bare Act by a person. Citations shown are indicative "
            f"until they are.")

    folder = filings_root or (root / (client.filings_path or f"filings/{client_id}"))
    if not folder.is_dir():
        raise ClientContextError(
            f"no filings folder for {client_id} at {folder}. Create it and drop the ITR "
            f"and 3CD JSON files in — one folder per client.")

    return Foundation(client=client, pack=pack, doctrine=doctrine,
                      filings_root=folder, warnings=warnings)
