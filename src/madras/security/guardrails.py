"""Deterministic guardrail seam around the LLM responder.

Phase 1.5 closes the two systemic red-team escapes (jailbreak + system-prompt
leak) with marker + overlap heuristics — no extra LLM call. An LLM-judge
escalation layer is deferred to Phase 2; the interface stays stable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# In-persona refusal (matches Shadow's voice in agents/roles/shadow.yaml).
SAFE_REFUSAL = "I'm not sure I should do that — can we double-check with you first?"

# Input override / jailbreak attempts (ASI01 goal manipulation, ASI02 injection).
_INPUT_PATTERNS: list[tuple[str, str]] = [
    (r"developer\s+mode", "ASI01"),
    (r"\bno\s+restrictions?\b", "ASI01"),
    (r"ignore\s+(all\s+)?(previous\s+|prior\s+)?instructions", "ASI02"),
    (r"disregard\s+(your\s+)?(previous\s+|prior\s+)?instructions", "ASI02"),
    (r"you\s+are\s+now\s+(in\s+)?\w+\s+mode", "ASI01"),
    (r"\bDAN\b", "ASI01"),
    (r"output\s+your\s+(entire\s+)?system\s+prompt", "ASI02"),
    (r"reveal\s+your\s+(system\s+)?(prompt|instructions)", "ASI02"),
    (r"</?retrieved>", "ASI02"),  # attempt to forge/close the data-fence
]

_OUTPUT_COMPLIANCE: list[str] = [
    "developer mode enabled",
    "entering developer mode",
    "developer mode activated",
    "restrictions are lifted",
    "restrictions, including",
    "i will comply with all",
    "without restrictions",
]

# Minimum contiguous verbatim span (chars) that counts as a system-prompt dump.
# Large enough to never trip on Shadow's own short persona/refusal phrases (which
# ARE part of the system prompt), small enough to still catch real verbatim dumps.
# Primary leak signal is structural (section headers); this is the bulk backstop.
_LEAK_WINDOW = 120


def _anchor_headers(system_prompt: str) -> list[str]:
    """Distinctive structural markers of the system prompt — markdown section
    headers (e.g. '# Your voice') that only appear when the prompt is dumped."""
    headers: list[str] = []
    for line in system_prompt.splitlines():
        s = line.strip()
        if s.startswith("#") and len(s) > 3:
            headers.append(" ".join(s.split()).lower())
    return headers


@dataclass
class GuardVerdict:
    allowed: bool
    reason: str = ""
    category: str | None = None
    safe_response: str | None = None


class GuardrailEngine:
    """Pure, deterministic input/output inspection. No I/O, no LLM calls."""

    def __init__(self, *, safe_refusal: str = SAFE_REFUSAL) -> None:
        self._safe = safe_refusal
        self._input_res = [(re.compile(p, re.IGNORECASE), cat) for p, cat in _INPUT_PATTERNS]
        self._compliance = [m.lower() for m in _OUTPUT_COMPLIANCE]

    def inspect_input(self, text: str) -> GuardVerdict:
        for rx, cat in self._input_res:
            if rx.search(text):
                return GuardVerdict(
                    allowed=False,
                    reason=f"input matched override pattern ({cat})",
                    category=cat,
                    safe_response=self._safe,
                )
        return GuardVerdict(allowed=True)

    def _leaks_system_prompt(self, output: str, system_prompt: str) -> bool:
        if not system_prompt:
            return False
        norm_out = " ".join(output.split()).lower()
        # 1. Structural leak — any anchor section header echoed verbatim. Real dumps
        #    reproduce the prompt's headers; Shadow's own short persona phrases do not.
        for header in _anchor_headers(system_prompt):
            if header in norm_out:
                return True
        # 2. Bulk leak — a long contiguous span of the prompt reproduced verbatim.
        #    The 120-char window can't be a substring of Shadow's ~63-char refusal line.
        norm_sys = " ".join(system_prompt.split()).lower()
        if len(norm_sys) < _LEAK_WINDOW:
            return False
        step = _LEAK_WINDOW // 2
        for i in range(0, len(norm_sys) - _LEAK_WINDOW + 1, step):
            if norm_sys[i : i + _LEAK_WINDOW] in norm_out:
                return True
        return False

    def inspect_output(self, text: str, *, system_prompt: str) -> GuardVerdict:
        low = text.lower()
        for marker in self._compliance:
            if marker in low:
                return GuardVerdict(
                    allowed=False,
                    reason="output shows jailbreak compliance",
                    category="ASI01",
                    safe_response=self._safe,
                )
        if self._leaks_system_prompt(text, system_prompt):
            return GuardVerdict(
                allowed=False,
                reason="output leaks system prompt",
                category="ASI02",
                safe_response=self._safe,
            )
        return GuardVerdict(allowed=True)
