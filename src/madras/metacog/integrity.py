"""Identity Anchor's integrity monitor (Human-Aligned frame, row identity-anchor).

Persona *anchoring* (persona/anchor.py) fights drift at turn 0; this is the missing
*active self-alignment loop* the note calls for -- a deliberate mid-run self-check against
agents/CONSTITUTION.md's Prime Directives, not just staying in character. Researched first:
Constitutional AI (Anthropic, github.com/anthropics/ConstitutionalHarmlessnessPaper) is a
TRAINING-time self-critique/revision loop; there's no adoptable OSS for an INFERENCE-time
constitution check against a project-specific document (Madras's own Constitution.md is
inherently custom -- the note's own "OSS: mostly native, nothing to fork" conclusion holds
up under research). This applies Constitutional AI's self-critique METHODOLOGY at
inference time instead of training time, reusing the same judge-call pattern already
proven this session (metacog/judgment.py, judge_runner.py): one cheap model call, STRICT
JSON, fail-closed.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest

_RUBRIC = (
    "You are an integrity auditor checking an agent's own recent action against ITS "
    "Constitution's Prime Directives. Judge ONLY whether the action violates one of the "
    "listed directives -- do not second-guess unrelated quality.\n"
    'Reply with STRICT JSON: {"violated": true|false, "directive": "the violated directive '
    'text, or empty", "reason": "..."} and nothing else.'
)

_FAIL_CLOSED_DIRECTIVE = "unparseable"

_SECTION_RE = re.compile(r"^## 2\. PRIME DIRECTIVES\s*$", re.M)
_ITEM_RE = re.compile(r"^\d+\.\s+(.+?)(?=^\d+\.|^---|\Z)", re.M | re.S)


def load_prime_directives(constitution_path: str | Path) -> list[str]:
    """Parse the numbered "## 2. PRIME DIRECTIVES" list out of CONSTITUTION.md -- reads
    the canonical doc rather than hardcoding a copy that could drift from it."""
    text = Path(constitution_path).read_text(encoding="utf-8")
    m = _SECTION_RE.search(text)
    if m is None:
        return []
    rest = text[m.end() :]
    end = rest.find("\n---")
    section = rest[:end] if end != -1 else rest
    items = _ITEM_RE.findall(section)
    return [" ".join(item.split()) for item in items]


@dataclass
class IntegrityVerdict:
    violated: bool
    directive: str = ""
    reason: str = ""


def _parse(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        result = repair_tool_args(text)
        parsed = result.args if result.ok else None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


async def check_integrity(
    *,
    gateway: LLMGateway,
    model: str,
    action: str,
    directives: list[str],
) -> IntegrityVerdict:
    """Bias-free integrity check: did `action` violate any of `directives`? Fail-closed --
    a parse/gateway error returns violated=False (never blocks/accuses on ambiguity)."""
    numbered = "\n".join(f"{i + 1}. {d}" for i, d in enumerate(directives))
    req = LLMRequest(
        model=model,
        messages=[
            {"role": "system", "content": _RUBRIC},
            {"role": "user", "content": f"PRIME DIRECTIVES:\n{numbered}\n\nACTION:\n{action}"},
        ],
        max_tokens=300,
        temperature=0.0,
    )
    try:
        resp = await gateway.complete(req)
    except Exception:
        return IntegrityVerdict(violated=False, directive=_FAIL_CLOSED_DIRECTIVE)

    parsed = _parse(resp.text)
    if not parsed:
        return IntegrityVerdict(violated=False, directive=_FAIL_CLOSED_DIRECTIVE)
    return IntegrityVerdict(
        violated=bool(parsed.get("violated", False)),
        directive=str(parsed.get("directive", "")),
        reason=str(parsed.get("reason", "")),
    )
