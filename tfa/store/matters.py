"""The identity of a matter, across runs.

This is the part of persistence that is actually hard. A run is a photograph;
the work is what people decide about what the photograph shows. If a re-run
cannot recognise the matter it decided about last week, one of two failures
follows: the register fills with duplicates, or a note recorded against one
figure silently attaches itself to a different one. The second is worse.

So a matter is keyed on what produced it, not on when:

    matter_key = sha256(client_id | rule_id | year | fingerprint)

where the fingerprint is every cell the finding cited, with its value. Then:

  * the same figures raise the same matter, and last week's disposition holds;
  * changed figures raise a *new* matter, and the old one is closed as
    superseded, with the lineage recorded, because "agreed, the client will pay
    it" said of ₹11.8 lakh is not a decision about ₹19.4 lakh;
  * a matter that stops being raised at all is closed as resolved — the filing
    that produced it was corrected, replaced or withdrawn.

Nothing here decides anything on a person's behalf. Closing a matter as
resolved or superseded is a statement about the filings, not about the merits,
and a person's own decisions are only ever appended to.
"""
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

# States a person sets, which a re-run must never overwrite.
HUMAN_STATES = ("accepted", "rejected", "amended")
# States the system sets from what the filings say.
SYSTEM_STATES = ("open", "resolved", "superseded")
ACTIONS = ("accept", "reject", "amend", "note", "reopen")


def _round(value: Any) -> Any:
    """Fingerprint on the figure, not on its float representation. Two runs
    that read the same rupee amount must fingerprint identically, and a
    difference of a paisa in a computed ratio is not a different matter."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    if isinstance(value, list):
        return [_round(v) for v in value]
    return str(value)


def fingerprint(evidence_cells: Sequence[Dict]) -> List[List[Any]]:
    """The cited cells, ordered so the same set always fingerprints the same.

    A finding that cites nothing — "no return of income is present for this
    year" — fingerprints as empty, which is correct: the matter is the absence,
    and it stays the same matter until the document arrives.
    """
    rows = [[str(c.get("fact", "")), str(c.get("ay", "")), _round(c.get("value"))]
            for c in (evidence_cells or [])]
    return sorted(rows, key=lambda r: (r[0], r[1], json.dumps(r[2], default=str)))


def matter_key(client_id: str, rule_id: str, ay: Optional[str],
               evidence_cells: Sequence[Dict]) -> str:
    payload = json.dumps({
        "client": client_id, "rule": rule_id, "ay": ay or "",
        "cells": fingerprint(evidence_cells),
    }, sort_keys=True, default=str)
    return "m_" + hashlib.sha256(payload.encode()).hexdigest()[:40]


@dataclass
class Reconciliation:
    """What one run did to the register."""
    raised_new: List[str]
    raised_again: List[str]
    reopened: List[str]
    superseded: List[Dict]          # {"old": key, "new": key}
    resolved: List[str]

    def as_dict(self) -> Dict:
        return {"new": self.raised_new, "again": self.raised_again,
                "reopened": self.reopened, "superseded": self.superseded,
                "resolved": self.resolved,
                "counts": {"new": len(self.raised_new),
                           "again": len(self.raised_again),
                           "reopened": len(self.reopened),
                           "superseded": len(self.superseded),
                           "resolved": len(self.resolved)}}

    @property
    def changed(self) -> bool:
        return bool(self.raised_new or self.superseded or self.resolved
                    or self.reopened)


def plan(existing: Sequence[Dict], raised: Sequence[Dict]) -> Dict[str, Any]:
    """Work out, in memory, what should happen to the register.

    `existing` is every matter already on file for this client, as dicts with
    at least matter_key, rule_id, ay and state. `raised` is what this run
    raised, each with matter_key, rule_id and ay. Kept separate from the SQL so
    the rule can be reasoned about and tested without a database.
    """
    by_key = {m["matter_key"]: m for m in existing}
    raised_keys = {r["matter_key"] for r in raised}

    new_keys, again, reopened = [], [], []
    for r in raised:
        current = by_key.get(r["matter_key"])
        if current is None:
            new_keys.append(r["matter_key"])
        else:
            again.append(r["matter_key"])
            if current.get("state") in ("resolved", "superseded"):
                reopened.append(r["matter_key"])

    # A matter for the same rule and year that is no longer raised, where the
    # same rule and year now raises something else, has been superseded rather
    # than resolved: the figures moved.
    replacement: Dict[tuple, str] = {}
    for r in raised:
        if r["matter_key"] in new_keys:
            replacement[(r["rule_id"], r.get("ay") or "")] = r["matter_key"]

    superseded, resolved = [], []
    for m in existing:
        if m["matter_key"] in raised_keys:
            continue
        if m.get("state") in ("resolved", "superseded"):
            continue                      # already closed; leave it alone
        successor = replacement.get((m["rule_id"], m.get("ay") or ""))
        if successor:
            superseded.append({"old": m["matter_key"], "new": successor})
        else:
            resolved.append(m["matter_key"])

    return {"new": new_keys, "again": again, "reopened": reopened,
            "superseded": superseded, "resolved": resolved}


def closing_reason(kind: str, run_label: str) -> str:
    if kind == "superseded":
        return (f"the run of {run_label} raised the same rule for the same year on "
                f"different figures; this matter is kept for the record and the "
                f"replacement carries the current numbers")
    return (f"not raised by the run of {run_label}: the figures that produced it are "
            f"no longer in the filings, or the filing that carried them has been "
            f"replaced")
