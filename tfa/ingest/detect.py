"""Working out what a file is from what is inside it.

The file name is a hint and nothing more. `ITR.json`, `final_3cd_v2 (1).json`
and `ay23.json` all turn up in practice, and a client who renames a file must
not thereby change what the analysis believes. The kind of return, the form,
the assessment year, the PAN and the name of the assessee are all read out of
the document, and each carries the pointer it was read from.
"""
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ..foundation import years as Y
from .pointer import Located, find_key, get, resolve_path, walk

ITR_FORMS = ["ITR1", "ITR2", "ITR3", "ITR4", "ITR5", "ITR6", "ITR7"]

# The 3CD statement travels inside either audit report form. Both carry the
# same Part A / clause structure; which one was used is itself a particular
# worth reporting, because Rule 6G ties the choice to how the accounts were
# audited.
THREECD_MARKERS = ["FORM3CA", "FORM3CB", "FORM3CA3CD", "FORM3CB3CD", "F3CA", "F3CB"]

PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


@dataclass
class Detection:
    kind: str                      # itr | 3cd | unknown
    form: str = ""                 # ITR-6, 3CA-3CD, 3CB-3CD …
    ay: Optional[str] = None       # normalised, e.g. "AY 2024-25"
    ay_raw: Optional[str] = None
    pan: Optional[str] = None
    name: Optional[str] = None
    root_pointer: str = ""         # where the form body starts
    evidence: Dict[str, str] = field(default_factory=dict)   # what -> pointer
    notes: List[str] = field(default_factory=list)

    @property
    def readable(self) -> bool:
        return self.kind in ("itr", "3cd")

    @property
    def label(self) -> str:
        if self.kind == "itr":
            return self.form or "ITR (form not stated)"
        if self.kind == "3cd":
            return self.form or "3CD (report form not stated)"
        return "not recognised"


def _first(doc: Any, names, **kw) -> Optional[Tuple[str, Any]]:
    hits = find_key(doc, names, limit=6, **kw)
    return hits[0] if hits else None


def _text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def detect_kind(doc: Any) -> Tuple[str, str, str]:
    """(kind, form, pointer to the body). Structure first, names second."""
    if isinstance(doc, dict):
        # ---- income-tax return: {"ITR": {"ITR6": {...}}} ------------------
        itr = doc.get("ITR")
        if isinstance(itr, dict):
            for key in itr:
                if key.upper().replace("-", "").replace("_", "") in ITR_FORMS:
                    canon = key.upper().replace("-", "").replace("_", "")
                    return "itr", f"ITR-{canon[3:]}", f"/ITR/{key}"
            return "itr", "ITR (form not stated)", "/ITR"
        for key in doc:
            if key.upper().replace("-", "").replace("_", "") in ITR_FORMS:
                canon = key.upper().replace("-", "").replace("_", "")
                return "itr", f"ITR-{canon[3:]}", f"/{key}"

        # ---- audit report: FORM3CA3CD / FORM3CB3CD ------------------------
        upper = {k.upper(): k for k in doc}
        for marker in ("FORM3CA3CD", "FORM3CB3CD"):
            if marker in upper:
                inner = doc[upper[marker]]
                form = "3CA-3CD" if "3CA" in marker else "3CB-3CD"
                return "3cd", form, f"/{upper[marker]}" if isinstance(inner, dict) else ""
        has_parta = any(k.upper() in ("PARTA", "PART_A") for k in doc)
        has_3ca = any(k.upper().startswith("FORM3CA") or k.upper() == "F3CA" for k in doc)
        has_3cb = any(k.upper().startswith("FORM3CB") or k.upper() == "F3CB" for k in doc)
        if has_parta and (has_3ca or has_3cb):
            return "3cd", "3CA-3CD" if has_3ca else "3CB-3CD", ""
        if has_parta and any(k.upper().startswith("FORM3CD") for k in doc):
            return "3cd", "3CD (report form not stated)", ""

    # ---- last resort: the form declares its own name ----------------------
    hit = _first(doc, ["FormName", "FormNameCode", "Form_Name"])
    if hit:
        name = str(hit[1]).upper().replace(" ", "")
        if "3CA" in name:
            return "3cd", "3CA-3CD", ""
        if "3CB" in name:
            return "3cd", "3CB-3CD", ""
        m = re.search(r"ITR-?(\d)", name)
        if m:
            return "itr", f"ITR-{m.group(1)}", ""
    return "unknown", "", ""


def detect(doc: Any) -> Detection:
    kind, form, root = detect_kind(doc)
    d = Detection(kind=kind, form=form, root_pointer=root)

    # ---- assessment year ---------------------------------------------------
    for names, note in (
        (["AssessmentYear", "AssmentYear", "AssessmentYr", "AsstYear"], ""),
        (["AY", "AYear"], "read from a field named only 'AY'"),
    ):
        for pointer, value in find_key(doc, names, limit=8):
            normalised = Y.normalise(value)
            if normalised:
                d.ay, d.ay_raw = normalised, _text(value)
                d.evidence["assessment_year"] = pointer
                if note:
                    d.notes.append(note)
                break
        if d.ay:
            break

    # A return that states only the previous year still identifies itself.
    if not d.ay:
        hit = _first(doc, ["FinancialYear", "PrevYear", "PreviousYear", "FY"])
        if hit:
            m = re.search(r"(\d{4})", str(hit[1]))
            if m:
                d.ay = Y.normalise(int(m.group(1)) + 1)
                d.ay_raw = _text(hit[1])
                d.evidence["assessment_year"] = hit[0]
                d.notes.append("assessment year derived from the financial year stated")

    # ---- PAN ---------------------------------------------------------------
    for pointer, value in find_key(doc, ["PAN", "PANofAssessee", "AssesseePAN",
                                         "PAN_No", "PanNumber"], limit=10):
        text = _text(value)
        if text and PAN_RE.match(text.upper()):
            d.pan = text.upper()
            d.evidence["pan"] = pointer
            break

    # ---- name --------------------------------------------------------------
    name = _assessee_name(doc, d)
    if name:
        d.name = name

    return d


def _assessee_name(doc: Any, d: Detection) -> Optional[str]:
    """Names arrive in three shapes: one string, a first/middle/last triple, or
    an organisation field. All three are stitched here so the fact matrix holds
    one name per year and can compare them."""
    for path in ("*.*.PartA_GEN1.PersonalInfo.AssesseeName",
                 "*.*.PersonalInfo.AssesseeName",
                 "PartA.AssesseeName", "*.PartA.AssesseeName", "AssesseeName"):
        for pointer, node in resolve_path(doc, path):
            if isinstance(node, dict):
                parts = [node.get(k) for k in
                         ("FirstName", "MiddleName", "SurNameOrOrgName", "LastName",
                          "OrgName", "NameOfAssessee")]
                text = " ".join(_text(p) or "" for p in parts if _text(p)).strip()
                if text:
                    d.evidence["name"] = pointer
                    return " ".join(text.split())
            elif _text(node):
                d.evidence["name"] = pointer
                return _text(node)

    for pointer, value in find_key(doc, ["NameOfAssessee", "AssesseeName",
                                         "SurNameOrOrgName", "NameOfPerson",
                                         "OrgName"], limit=6):
        text = _text(value)
        if text and len(text) > 1:
            d.evidence["name"] = pointer
            return text
    return None


def size_of(doc: Any) -> Dict[str, int]:
    """How much is actually in the file. A 3CD with forty populated leaves has
    not been filled in, whatever its clause list claims."""
    leaves = 0
    populated = 0
    for _, value in walk(doc):
        leaves += 1
        if value not in (None, "", []):
            populated += 1
    return {"leaves": leaves, "populated": populated}
