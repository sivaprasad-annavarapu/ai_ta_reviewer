"""Model prices, and what a run actually cost.

Rates are per million tokens, first-party Anthropic API, as published. They are
data, not a guess: a review that reports its own cost wrongly is worse than one
that does not report it at all.

Cache reads are billed at a tenth of the input rate and cache writes at 1.25x,
which is why the content-addressed cache is the first cost lever rather than the
last.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# $ per million tokens. Update alongside any change to the model roster.
RATES: Dict[str, Dict[str, float]] = {
    "claude-opus-5":     {"input": 5.00, "output": 25.00},
    "claude-opus-4-8":   {"input": 5.00, "output": 25.00},
    "claude-sonnet-5":   {"input": 2.00, "output": 10.00},
    "claude-haiku-4-5":  {"input": 1.00, "output": 5.00},
    "claude-fable-5-1":  {"input": 10.00, "output": 50.00},
}

CACHE_READ_MULTIPLIER = 0.10
CACHE_WRITE_MULTIPLIER = 1.25
BATCH_MULTIPLIER = 0.50


def rate(model: str, kind: str) -> float:
    for known, prices in RATES.items():
        if model.startswith(known):
            return prices[kind]
    return 0.0


def cost(model: str, input_tokens: int = 0, output_tokens: int = 0,
         cache_read: int = 0, cache_write: int = 0, batch: bool = False) -> float:
    total = (
        input_tokens / 1e6 * rate(model, "input")
        + output_tokens / 1e6 * rate(model, "output")
        + cache_read / 1e6 * rate(model, "input") * CACHE_READ_MULTIPLIER
        + cache_write / 1e6 * rate(model, "input") * CACHE_WRITE_MULTIPLIER
    )
    return total * (BATCH_MULTIPLIER if batch else 1.0)


@dataclass
class CallCost:
    label: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    cache_write: int = 0
    cached: bool = False
    ms: float = 0.0

    @property
    def dollars(self) -> float:
        if self.cached:
            return 0.0
        return cost(self.model, self.input_tokens, self.output_tokens,
                    self.cache_read, self.cache_write)

    def as_dict(self) -> Dict:
        return {
            "label": self.label, "model": self.model,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "cache_read": self.cache_read, "cache_write": self.cache_write,
            "cached": self.cached, "ms": self.ms,
            "usd": round(self.dollars, 6),
        }


def summarise(calls: List[CallCost]) -> Dict:
    """Per-call attribution, so the expensive question is identifiable rather
    than averaged into a single number nobody can act on."""
    billed = [c for c in calls if not c.cached]
    by_label: Dict[str, Dict] = {}
    for c in billed:
        stem = c.label.split("#")[0]
        row = by_label.setdefault(stem, {"calls": 0, "input": 0, "output": 0, "usd": 0.0,
                                         "model": c.model})
        row["calls"] += 1
        row["input"] += c.input_tokens
        row["output"] += c.output_tokens
        row["usd"] = round(row["usd"] + c.dollars, 6)
    ranked = sorted(by_label.items(), key=lambda kv: -kv[1]["usd"])
    return {
        "total_usd": round(sum(c.dollars for c in billed), 4),
        "billed_calls": len(billed),
        "cached_calls": sum(1 for c in calls if c.cached),
        "input_tokens": sum(c.input_tokens for c in billed),
        "output_tokens": sum(c.output_tokens for c in billed),
        "by_rule": [{"rule": k, **v} for k, v in ranked],
    }
