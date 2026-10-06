"""The question box.

A question asked of the filings, answered from the fact matrix and cited back
to it. It records no conclusion and changes no finding: the register is built
by the rules, and an answer here is a reading aid, not a verdict.

Answers come back as short paragraphs and numbered points, structured by the
model rather than parsed out of prose, so the screen can render exactly those
two shapes and nothing else.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ..facts.matrix import Cell, FactMatrix
from .client import ModelClient
from .prompts import (
    CITATION_PROPERTY, DIRECTIVE_PROPERTY, SYSTEM_RULES, render_findings,
    render_material, resolve_citations, select,
)

ANSWER_SCHEMA = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "description": "The answer, as two or three short paragraphs and at most "
                           "one numbered list. Put the direct answer first.",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string", "enum": ["paragraph", "list"]},
                    "text": {"type": "string",
                             "description": "For a paragraph. Cite handles inline in "
                                            "square brackets, e.g. [C12]."},
                    "items": {"type": "array", "items": {"type": "string"},
                              "description": "For a list. One point each, cited."},
                },
                "required": ["type"],
            },
        },
        "sufficient": {"type": "boolean",
                       "description": "False where the matrix does not settle the "
                                      "question."},
        "follow_up": {"type": "string",
                      "description": "If not sufficient, the document or figure that "
                                     "would settle it."},
        "citations": CITATION_PROPERTY,
        "directive_text_found": DIRECTIVE_PROPERTY,
    },
    "required": ["blocks", "sufficient", "citations", "directive_text_found"],
}


@dataclass
class ChatAnswer:
    ok: bool
    answer: str = ""
    blocks: List[Dict] = field(default_factory=list)
    references: List[Dict] = field(default_factory=list)
    sufficient: bool = True
    follow_up: str = ""
    directive_text: bool = False
    reason: str = ""
    cells_searched: int = 0
    years: List[str] = field(default_factory=list)
    cached: bool = False

    def as_dict(self) -> Dict:
        return {
            "ok": self.ok, "answer": self.answer, "blocks": self.blocks,
            "references": self.references, "sufficient": self.sufficient,
            "follow_up": self.follow_up, "directive_text": self.directive_text,
            "reason": self.reason, "cells_searched": self.cells_searched,
            "years": self.years, "cached": self.cached,
        }


def ask(question: str, matrix: FactMatrix, trends: Dict, findings: Sequence,
        client: ModelClient, doctrine=None) -> ChatAnswer:
    question = " ".join(str(question or "").split())
    if not question:
        return ChatAnswer(False, reason="no question was asked")
    if client is None or not client.available:
        return ChatAnswer(False, reason=(client.settings.unavailable_reason() if client
                                         else "no model client is configured"))
    if not matrix.years:
        return ChatAnswer(False, reason="no assessment year could be read from the "
                                        "filings, so there is nothing to answer from")

    material = render_material(matrix, select(matrix), trends=trends,
                               extra=render_findings(findings or []))

    prompt = f"""\
THE QUESTION
{question}

WHAT YOU MAY USE
The fact matrix below, and the findings already established. Nothing else. If the
matrix does not settle the question, say so and name what would.

You are not being asked for a conclusion on the assessee's affairs, and you must not
record one. Answer the question, cite the cells, and stop.

{material.text}

AVAILABLE CELL HANDLES: {material.handles}
"""

    reply = client.structured(
        system=SYSTEM_RULES, user=prompt, schema=ANSWER_SCHEMA,
        tool_name="record_answer",
        tool_description="Answer the question from the fact matrix, with citations.",
        label="question", max_tokens=2500)

    if reply.failed:
        return ChatAnswer(False, reason=reply.reason,
                          cells_searched=material.count)

    data = reply.data or {}
    citations, dropped = resolve_citations(data.get("citations"), material.cells)
    numbered = {c.ref: i + 1 for i, c in enumerate(citations)}

    blocks = []
    for block in data.get("blocks", [])[:8]:
        kind = block.get("type")
        if kind == "list":
            items = [_renumber(str(i), numbered) for i in (block.get("items") or [])]
            if items:
                blocks.append({"type": "list", "items": items})
        else:
            text = _renumber(str(block.get("text", "")), numbered)
            if text.strip():
                blocks.append({"type": "paragraph", "text": text})

    plain = "\n\n".join(b.get("text", "") if b["type"] == "paragraph"
                        else "\n".join(f"{i + 1}. {t}"
                                       for i, t in enumerate(b["items"]))
                        for b in blocks)

    references = [{"n": numbered[c.ref], "handle": c.ref,
                   "locator": c.render(),
                   "fact": matrix.spec(c.fact_id).label if matrix.spec(c.fact_id)
                           else c.fact_id}
                  for c in citations]

    return ChatAnswer(
        ok=True, answer=plain, blocks=blocks, references=references,
        sufficient=bool(data.get("sufficient", True)),
        follow_up=" ".join(str(data.get("follow_up", "")).split()),
        directive_text=bool(data.get("directive_text_found")),
        cells_searched=material.count, years=list(matrix.years),
        cached=reply.cached)


def _renumber(text: str, numbered: Dict[str, int]) -> str:
    """The model cites `[C12]`; the reader sees `[3]` against a numbered list.
    A handle that did not resolve is removed rather than shown as a dead
    reference."""
    import re

    def swap(match):
        handles = [h.strip().upper() for h in match.group(1).split(",")]
        numbers = [str(numbered[h]) for h in handles if h in numbered]
        return f"[{','.join(numbers)}]" if numbers else ""

    return re.sub(r"\[([Cc]\d+(?:\s*,\s*[Cc]\d+)*)\]", swap, text).replace("  ", " ")
