"""The model client.

Four things this wrapper exists to guarantee, each of them a constitutional
obligation rather than a nicety:

  * Determinism (VI.1) — temperature 0, a pinned model, and a content-addressed
    cache, so re-running an unchanged corpus returns the same words.
  * Structured output — every call is forced through a tool schema, so a verdict
    is a closed enum rather than prose we have to parse hopefully.
  * Fail closed (V.2) — an API error, a budget stop or a missing key never
    invents a verdict. The caller gets None and a reason, and the check becomes
    INSUFFICIENT_EVIDENCE.
  * A spend ceiling — a review of a large folder cannot quietly cost a fortune.
"""
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import Settings
from ..foundation.hashing import hash_obj
from .pricing import CallCost, summarise


@dataclass
class Budget:
    """Per-run ceiling. Exhaustion degrades the run; it never crashes it.

    `max_calls` of 0 means no calls are permitted, and a negative value means no
    ceiling. Zero used to mean "unlimited", which is the wrong way round for a
    spend guard: the value that reads as "stop" must not mean "go".
    """
    max_calls: int
    calls: int = 0
    cached: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    errors: List[str] = field(default_factory=list)
    stopped: bool = False
    calls_log: List[CallCost] = field(default_factory=list)

    @property
    def exhausted(self) -> bool:
        if self.max_calls < 0:
            return False
        return self.calls >= self.max_calls

    def manifest(self) -> Dict:
        out = {
            "model_calls": self.calls, "cache_hits": self.cached,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "budget": self.max_calls, "budget_exhausted": self.exhausted,
            "errors": self.errors[:10],
        }
        out["cost"] = summarise(self.calls_log)
        return out


@dataclass
class Reply:
    ok: bool
    data: Optional[Dict] = None
    reason: str = ""
    cached: bool = False
    model: str = ""
    ms: float = 0.0

    @property
    def failed(self) -> bool:
        return not self.ok


class ModelClient:
    def __init__(self, settings: Settings, budget: Optional[Budget] = None,
                 collect_only: bool = False):
        self.settings = settings
        self.budget = budget or Budget(max_calls=settings.max_model_calls)
        self._client = None
        self._cache = settings.cache_dir
        self._cache.mkdir(parents=True, exist_ok=True)
        # In collect mode nothing is sent: requests are recorded for the batch
        # pre-warm, and every check reports itself as deferred rather than
        # guessing an answer.
        self.collect_only = collect_only
        self.collected: Dict[str, Dict] = {}

    # ------------------------------------------------------------------ cache
    def _cache_path(self, digest: str) -> Path:
        short = digest.split(":")[-1]
        return self._cache / short[:2] / f"{short}.json"

    def _read_cache(self, digest: str) -> Optional[Dict]:
        path = self._cache_path(digest)
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text())["response"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

    def _write_cache(self, digest: str, response: Dict, meta: Dict) -> None:
        path = self._cache_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(
                {"digest": digest, "response": response, "meta": meta,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
                indent=2))
        except OSError:
            pass

    # ----------------------------------------------------------------- client
    def _sdk(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=self.settings.api_key,
                timeout=self.settings.request_timeout,
                max_retries=2,
            )
        return self._client

    @property
    def available(self) -> bool:
        return self.settings.ai_available

    # ------------------------------------------------------------------- call
    def structured(
        self,
        system: str,
        user: str,
        schema: Dict,
        tool_name: str = "record",
        tool_description: str = "Record the result.",
        fast: bool = False,
        max_tokens: Optional[int] = None,
        label: str = "",
        model: Optional[str] = None,
        effort: Optional[str] = None,
    ) -> Reply:
        """One call, one structured result. `schema` is a JSON Schema object.

        `label` attributes the spend to a check. `model` lets a check that does
        not need the strongest model say so explicitly, per check rather than
        globally.
        """
        model = model or (self.settings.model_fast if fast else self.settings.model)
        if not self.available:
            return Reply(False, reason=self.settings.unavailable_reason())

        # Newer models are deterministic by default and reject `temperature`
        # outright. Zero therefore means "do not send it" rather than "send 0",
        # and a non-zero value is only ever a deliberate variance experiment.
        request = {
            "model": model,
            "max_tokens": max_tokens or self.settings.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "tools": [{"name": tool_name, "description": tool_description,
                       "input_schema": schema}],
            "tool_choice": {"type": "tool", "name": tool_name},
        }
        if self.settings.temperature:
            request["temperature"] = self.settings.temperature
        chosen_effort = effort or self.settings.effort
        if chosen_effort:
            request["output_config"] = {"effort": chosen_effort}
        digest = hash_obj(request)

        hit = self._read_cache(digest)
        if hit is not None:
            self.budget.cached += 1
            self.budget.calls_log.append(
                CallCost(label=label or tool_name, model=model, cached=True))
            return Reply(True, hit, cached=True, model=model)

        if self.collect_only:
            self.collected[digest] = request
            return Reply(False, reason=(
                "deferred to the batch pre-warm; this matter has not yet been "
                "assessed by model judgment"), model=model)

        if self.budget.exhausted:
            self.budget.stopped = True
            return Reply(False, reason=(
                f"the per-run ceiling of {self.budget.max_calls} model calls was "
                f"reached, so this matter was not assessed by model judgment"))

        started = time.perf_counter()
        try:
            self.budget.calls += 1
            response = self._sdk().messages.create(**request)
        except Exception as exc:
            # One narrow retry: if the model rejects a sampling parameter we
            # sent, drop it and try again rather than losing the whole check.
            # Anything else fails closed.
            message = str(exc)
            retried = False
            if "temperature" in message and "deprecated" in message and \
                    "temperature" in request:
                request.pop("temperature", None)
                digest = hash_obj(request)
                try:
                    response = self._sdk().messages.create(**request)
                    retried = True
                except Exception as exc2:
                    exc, message = exc2, str(exc2)
            if not retried:
                reason = f"{type(exc).__name__}: {message}"
                self.budget.errors.append(reason)
                return Reply(False, reason=f"the model could not be reached ({reason})",
                             model=model)

        elapsed = round((time.perf_counter() - started) * 1000, 1)
        usage = getattr(response, "usage", None)
        entry = CallCost(label=label or tool_name, model=model, ms=elapsed)
        if usage:
            entry.input_tokens = getattr(usage, "input_tokens", 0) or 0
            entry.output_tokens = getattr(usage, "output_tokens", 0) or 0
            entry.cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            entry.cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            self.budget.input_tokens += entry.input_tokens
            self.budget.output_tokens += entry.output_tokens
        self.budget.calls_log.append(entry)

        payload = None
        for block in response.content:
            if getattr(block, "type", "") == "tool_use" and block.name == tool_name:
                payload = block.input
                break
        if payload is None:
            return Reply(False, reason="the model returned no structured result",
                         model=model, ms=elapsed)

        self._write_cache(digest, payload, {"model": model, "ms": elapsed})
        return Reply(True, payload, model=model, ms=elapsed)
