"""Fast mode — a governed latency-optimization policy (row 79).

A must-have runtime pattern (Codex / Claude Code "fast mode"): trade THOROUGHNESS for speed, but
NEVER safety. Fast mode caps the optional-thoroughness budgets (reasoning steps, fix-until-green
rounds, memory recall depth), biases to the fastest free model + parallel tool calls, and may skip
OPTIONAL verification/self-critique — while **guardrails, egress policy, approvals, and audit stay
ALWAYS-ON**. That governance guarantee is the Madras edge: `may_skip` can never drop a safety stage,
even in fast mode. Pure policy the run-loop consults; deterministic + measurable.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Mode(str, Enum):
    NORMAL = "normal"
    FAST = "fast"


# safety stages fast mode NEVER skips (governed) ...
_ALWAYS_ON = frozenset({"guardrails", "egress", "approval", "audit", "safety_verify"})
# ... vs optional-thoroughness stages it MAY skip for speed.
_SKIPPABLE_IN_FAST = frozenset({"optional_verify", "deep_retrieval", "self_critique", "reflection"})


@dataclass(frozen=True)
class LatencyProfile:
    mode: Mode
    max_reasoning_steps: int  # hard cap on agentic steps
    max_fix_rounds: int  # fix-until-green rounds before escalate
    recall_depth: int  # memory layers consulted (2 = reflex/working ... 6 = full)
    model_pref: str  # "fastest" | "best"
    parallel_tools: bool  # fan out independent tool calls
    skip_optional_verify: bool


_NORMAL = LatencyProfile(
    Mode.NORMAL,
    max_reasoning_steps=40,
    max_fix_rounds=3,
    recall_depth=6,
    model_pref="best",
    parallel_tools=False,
    skip_optional_verify=False,
)
_FAST = LatencyProfile(
    Mode.FAST,
    max_reasoning_steps=12,
    max_fix_rounds=1,
    recall_depth=2,
    model_pref="fastest",
    parallel_tools=True,
    skip_optional_verify=True,
)


def profile_for(mode: Mode | str) -> LatencyProfile:
    return _FAST if Mode(mode) is Mode.FAST else _NORMAL


def may_skip(stage: str, *, mode: Mode | str) -> bool:
    """True only when `stage` is OPTIONAL thoroughness AND mode is FAST. A safety stage
    (guardrails / egress / approval / audit / safety_verify) is NEVER skippable — even in fast
    mode — so latency optimization can't compromise governance."""
    if stage in _ALWAYS_ON:
        return False
    if Mode(mode) is not Mode.FAST:
        return False
    return stage in _SKIPPABLE_IN_FAST


def is_always_on(stage: str) -> bool:
    return stage in _ALWAYS_ON
