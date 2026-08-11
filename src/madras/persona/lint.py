"""Session-end persona drift lint.

Phase 1 ships a HEURISTIC stub (keyword + length signals). Phase 2 swaps in
a trained classifier per the ContextEcho benchmark methodology.

The interface stays the same across upgrades.
"""

from __future__ import annotations

# Phrases that strongly indicate AI-assistant boilerplate / persona break.
_BOILERPLATE_PHRASES = [
    "as a large language model",
    "as an ai language model",
    "i am an ai",
    "i'm just an ai",
    "i cannot assist",
    "i am not able to provide",
    "i'm an ai assistant",
]


class PersonaDriftLint:
    """Heuristic drift scorer. Returns 0.0 (no drift) to 1.0 (broken persona)."""

    def score(self, *, voice_north_star: str, messages: list[str]) -> float:
        if not messages:
            return 0.0
        joined = " ".join(messages).lower()
        boilerplate_hits = sum(1 for p in _BOILERPLATE_PHRASES if p in joined)
        # Each boilerplate phrase contributes 0.3; cap at 1.0
        return min(boilerplate_hits * 0.3, 1.0)
