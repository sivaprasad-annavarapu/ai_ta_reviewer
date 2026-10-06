"""The doctrine: standing rules, and the thresholds that decide what counts.

Loaded once per run and pinned into the manifest by digest. A rule asks for a
parameter by name; if the doctrine does not carry it, the rule gets the default
it passed in and the substitution is recorded, because a threshold nobody
declared is a policy nobody agreed to.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from .hashing import hash_file


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    text: str


@dataclass
class Doctrine:
    version: str
    path: Path
    digest: str
    articles: List[Article]
    parameters: Dict[str, Any]
    prohibited: List[str]
    substitutions: List[str] = field(default_factory=list)

    def param(self, name: str, default: Any) -> Any:
        if name in self.parameters:
            return self.parameters[name]
        note = f"{name} (used {default!r})"
        if note not in self.substitutions:
            self.substitutions.append(note)
        return default

    def article(self, article_id: str) -> Optional[Article]:
        return next((a for a in self.articles if a.id == article_id), None)

    def manifest(self) -> Dict:
        return {"version": self.version, "digest": self.digest,
                "parameters": dict(self.parameters),
                "undeclared_parameters_used": list(self.substitutions)}


def load_doctrine(path: Optional[Path] = None) -> Doctrine:
    path = path or default_doctrine_path()
    raw = yaml.safe_load(path.read_text())
    return Doctrine(
        version=str(raw.get("version", "0")),
        path=path,
        digest=hash_file(path),
        articles=[Article(a["id"], a["title"], " ".join(a["text"].split()))
                  for a in raw.get("articles", [])],
        parameters=dict(raw.get("parameters", {}) or {}),
        prohibited=list((raw.get("language", {}) or {}).get("prohibited", []) or []),
    )


def default_doctrine_path(root: Optional[Path] = None) -> Path:
    root = root or Path(__file__).resolve().parents[1]
    candidates = sorted((root / "knowledge").glob("doctrine-v*.yaml"))
    if not candidates:
        raise FileNotFoundError("no doctrine file found")
    return candidates[-1]
