"""Overcoming Engine's push-vs-quit decision (row overcoming-engine).

The note's own framing ("re-wire the dead impasse detector") was stale -- SelfMonitor/
detect_impasse and judge_decision are both already live in graph/tool_loop.py, but
their output only ever became NUDGE TEXT: the agent gets told "you might be biased"
and keeps going regardless. The real gap: nothing converts a sunk-cost-biased verdict
into an actual decision. This is that decision -- pure, one branch, no new tracking
infra (resilience/streak/breakthrough stay explicitly BUILD, not faked here).
"""

from __future__ import annotations

from enum import Enum


class PersistenceDecision(str, Enum):
    PUSH = "push"  # keep going -- being hard isn't a reason to quit (grit)
    WISE_QUIT = "wise_quit"  # the retries are sunk-cost, not new evidence -- stop


def decide_persistence(verdict: object | None) -> PersistenceDecision:
    """`verdict` is a `metacog.judgment.JudgmentVerdict` (or None if the judge call
    failed/was never made). Only a CONFIRMED sunk-cost bias triggers a quit -- any
    other bias kind (confirmation/recency/halo), or no bias at all, means push through:
    quitting isn't warranted just because a bias check ran."""
    if verdict is None:
        return PersistenceDecision.PUSH
    biased = getattr(verdict, "biased", False)
    kind = getattr(verdict, "bias_kind", "")
    if biased and kind == "sunk_cost":
        return PersistenceDecision.WISE_QUIT
    return PersistenceDecision.PUSH
