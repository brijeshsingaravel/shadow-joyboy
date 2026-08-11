"""Resource Awareness's cognitive-mode selector (Human-Aligned frame, row resource-awareness).

Researched first: no adoptable OSS owns "urgency -> mode -> model tradeoff" for a specific
codebase's own router (Headroom/Apache-2.0 addresses token COMPRESSION, a different, already
partly-built concern -- see Context Discipline; microsoft/best-route-llm and the academic
SelfBudgeter/TALE/BudgetThinker line confirm the PATTERN -- classify task shape, then select
compute -- but Madras already has the router (`llm/select.py::select_model`'s `tradeoff`
param, 0=quality..10=cheapest) and the SelfBudgeter-style classifier was simply never built:
`tradeoff` was a dead knob, hardcoded to the default 7 on every real turn (grep-confirmed:
only exercised in eval_/proving_ground's routing_resilience.py suite, never live).

Deterministic, no LLM call (the classification itself must be cheap -- an LLM call to decide
"how much to spend" would defeat the point).
"""

from __future__ import annotations

_URGENT_MARKERS = (
    "asap",
    "urgent",
    "urgently",
    "quickly",
    "right now",
    "hurry",
    "immediately",
    "fast",
)
_DEEP_MARKERS = (
    "thoroughly",
    "carefully",
    "in depth",
    "in-depth",
    "comprehensive",
    "no rush",
    "take your time",
    "deep dive",
    "rigorously",
    "exhaustive",
)

# tradeoff: 0 = quality-first .. 10 = cheapest/fastest (auto_router.py's own scale)
_TRADEOFF_FOR_MODE = {"urgent": 9, "normal": 7, "deep": 3}


def classify_urgency(user_input: str) -> str:
    """ "urgent" | "normal" | "deep" from explicit language in the user's own message.
    Urgency markers win over depth markers when both are present (a time-pressured
    request for depth is still time-pressured)."""
    text = (user_input or "").lower()
    if any(marker in text for marker in _URGENT_MARKERS):
        return "urgent"
    if any(marker in text for marker in _DEEP_MARKERS):
        return "deep"
    return "normal"


def tradeoff_for_mode(mode: str) -> int:
    return _TRADEOFF_FOR_MODE.get(mode, _TRADEOFF_FOR_MODE["normal"])


def tradeoff_for_input(user_input: str) -> int:
    """The one-call convenience most callers want: user_input -> tradeoff."""
    return tradeoff_for_mode(classify_urgency(user_input))
