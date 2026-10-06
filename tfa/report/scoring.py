"""Scoring a run against defects we planted ourselves.

The generator writes a manifest of exactly what is wrong with the synthetic
filings. This module matches the findings against it, and reports what was
missed as prominently as what was caught — a rule set is only as good as its
recall on defects somebody deliberately put there.

Findings the manifest does not mention are listed separately rather than
counted as false. Some of them are real conditions the generator produced
without meaning to; each needs judging on its merits.
"""
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence


@dataclass
class Score:
    planted: int
    detected: int
    matched: List[Dict] = field(default_factory=list)
    missed: List[Dict] = field(default_factory=list)
    additional: List[Dict] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return (self.detected / self.planted) if self.planted else 0.0


def score_run(result, manifest_path: Path) -> Optional[Score]:
    if not manifest_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    planted = manifest.get("planted", [])
    raised = [f for f in result.findings
              if f.verdict in ("FAIL", "INSUFFICIENT_EVIDENCE")]

    matched: List[Dict] = []
    missed: List[Dict] = []
    used: set = set()

    for defect in planted:
        expected = defect.get("expected_rule")
        ay = defect.get("ay")
        hit = next((f for f in raised
                    if f.rule_id == expected and (f.ay is None or f.ay == ay)), None)
        if hit is None:
            # A defect planted in one year is often raised on the pair, which is
            # recorded against the later year — accept either side of the pair.
            hit = next((f for f in raised if f.rule_id == expected), None)
        if hit is None:
            missed.append(defect)
            continue
        used.add(hit.instance_id)
        matched.append({
            "defect_id": defect.get("defect_id"), "ay": ay,
            "where": defect.get("where"), "description": defect.get("description"),
            "instance": hit.instance_id, "verdict": hit.verdict,
            "expected_rule": expected})

    additional = [{"instance": f.instance_id, "verdict": f.verdict,
                   "severity": f.severity, "ay": f.ay,
                   "statement": f.statement[:240]}
                  for f in raised
                  if f.instance_id not in used and f.verdict == "FAIL"]

    return Score(planted=len(planted), detected=len(matched), matched=matched,
                 missed=missed, additional=additional)
