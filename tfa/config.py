"""Runtime settings.

Read from the OS environment first, then from the project's `env` file for
anything the environment does not already define. The file is deliberately not
hidden — it is meant to be seen and edited — and it is deliberately lower
priority than a real `export`, so a shell or a secret manager always wins.

Nothing in this module fails at import time. A run with no API key is a valid
run: every deterministic rule still executes, and the parts that need a model
are reported as not assessed rather than silently omitted — a gap is never a
non-event.
"""
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

ROOT = Path(__file__).resolve().parents[1]

DEFAULT_MODEL = "claude-opus-5"
DEFAULT_MODEL_FAST = "claude-sonnet-5"

# Keys: TFA_ENV_FILE (set on the server), else the project `env` if present,
# else the shared key folder ~/GRCKEY/envs/tfa.env — see ~/GRCKEY/AWF_CONVENTIONS.md.
ENV_FILE = Path(os.environ.get("TFA_ENV_FILE") or (
    ROOT / "env" if (ROOT / "env").is_file() else Path.home() / "GRCKEY" / "envs" / "tfa.env"))


def _read_env_file(path: Path) -> Dict[str, str]:
    """Parse KEY=VALUE lines. Tolerates `export KEY=value`, quotes and comments
    so the same file can also be `source`d from a shell."""
    values: Dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return values
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().split(" #")[0].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


FILE_ENV = _read_env_file(ENV_FILE)


def _env(name: str, default: str = "") -> str:
    """The shell wins; the file fills gaps; the default is the last resort."""
    shell = (os.environ.get(name) or "").strip()
    if shell:
        return shell
    return (FILE_ENV.get(name) or default).strip()


def _int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = _env(name)
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    api_key: str = ""
    model: str = DEFAULT_MODEL
    model_fast: str = DEFAULT_MODEL_FAST
    temperature: float = 0.0
    max_model_calls: int = 300       # 0 permits none; -1 removes the ceiling
    cache_dir: Path = ROOT / ".tfa-cache"
    filings_dir: Path = ROOT / "filings"
    run_mode: str = "simulation"
    max_tokens: int = 3000
    request_timeout: float = 180.0
    key_source: str = "unset"        # shell | env file | unset
    effort: str = ""                 # "", low, medium, high, xhigh, max

    @property
    def ai_available(self) -> bool:
        return bool(self.api_key)

    @property
    def redacted_key(self) -> str:
        if not self.api_key:
            return "(not set)"
        return f"{self.api_key[:7]}…{self.api_key[-4:]}"

    def unavailable_reason(self) -> str:
        if self.ai_available:
            return ""
        return (f"ANTHROPIC_API_KEY is empty in both the shell and {ENV_FILE.name}, so "
                "the rules and features that need model judgment were not run")

    def manifest(self) -> Dict:
        """What goes into the run manifest. Never the key itself."""
        return {
            "ai_available": self.ai_available,
            "model": self.model if self.ai_available else None,
            "model_fast": self.model_fast if self.ai_available else None,
            "temperature": (self.temperature if self.temperature else
                            "not sent (model is deterministic by default)"),
            "max_model_calls": self.max_model_calls,
            "effort": self.effort or "high (default)",
            "cache": str(self.cache_dir.name),
            "key_source": self.key_source,
        }


def load_settings() -> Settings:
    cache = _env("TFA_CACHE_DIR", ".tfa-cache")
    filings = _env("TFA_FILINGS_DIR", "filings")
    key = _env("ANTHROPIC_API_KEY")
    if not key:
        source = "unset"
    elif (os.environ.get("ANTHROPIC_API_KEY") or "").strip():
        source = "shell"
    else:
        source = f"{ENV_FILE.name} file"
    return Settings(
        api_key=key,
        key_source=source,
        model=_env("TFA_MODEL", DEFAULT_MODEL),
        model_fast=_env("TFA_MODEL_FAST", DEFAULT_MODEL_FAST),
        temperature=_float("TFA_TEMPERATURE", 0.0),
        max_model_calls=_int("TFA_MAX_MODEL_CALLS", 300),
        cache_dir=(Path(cache) if Path(cache).is_absolute() else ROOT / cache),
        filings_dir=(Path(filings) if Path(filings).is_absolute() else ROOT / filings),
        run_mode=_env("TFA_RUN_MODE", "simulation"),
        effort=_env("TFA_EFFORT", ""),
        max_tokens=_int("TFA_MAX_TOKENS", 3000),
        request_timeout=_float("TFA_TIMEOUT", 180.0),
    )


SETTINGS = load_settings()


def refresh() -> Settings:
    """Re-read settings, including the `env` file, so editing it and pressing
    Run picks up the change with no restart."""
    global SETTINGS, FILE_ENV
    FILE_ENV = _read_env_file(ENV_FILE)
    SETTINGS = load_settings()
    return SETTINGS
