"""Model-aware orchestration policy.

Scales subagent intensity by model tier: FREE/local models work inline by default
and delegate/verify sparingly (still showcasing the capability); PREMIUM models fan
out aggressively. The per-tier caps double as the circuit breaker.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class OrchestrationTier(str, Enum):
    FREE = "free"
    STANDARD = "standard"
    PREMIUM = "premium"


@dataclass(frozen=True)
class OrchestrationBudget:
    max_concurrent: int
    max_total: int  # per turn (hard circuit-breaker cap)
    max_verify_rounds: int
    delegation_guidance: str  # injected into the supervisor prompt


_BUDGETS: dict[OrchestrationTier, OrchestrationBudget] = {
    OrchestrationTier.FREE: OrchestrationBudget(
        2,
        4,
        1,
        "You are on a fast, lightweight model. Work INLINE by default. Delegate to "
        "subagents or run verification only when a task is clearly parallelizable or "
        "correctness-critical — keep it minimal. For ANY multi-step task, FIRST call "
        "plan_write with the ordered steps, then call plan_check to mark each item done "
        "as you complete it.",
    ),
    OrchestrationTier.STANDARD: OrchestrationBudget(
        3, 10, 2, "Delegate to subagents and run verification when it improves quality or speed."
    ),
    OrchestrationTier.PREMIUM: OrchestrationBudget(
        4,
        16,
        3,
        "Leverage parallel fan-out and adversarial verification aggressively for "
        "thoroughness and correctness.",
    ),
}


def tier_for_model(model: str) -> OrchestrationTier:
    m = (model or "").lower()
    if "opus" in m:
        return OrchestrationTier.PREMIUM
    if "sonnet" in m or "gpt-4" in m or "gpt-5" in m or "gpt-4o" in m:
        return OrchestrationTier.STANDARD
    if "haiku" in m:
        return OrchestrationTier.STANDARD
    # local/free models (llama-70b, deepseek-r1, nemotron-*, gemini-flash, qwen*) -> FREE
    return OrchestrationTier.FREE


def budget_for(model: str) -> OrchestrationBudget:
    return _BUDGETS[tier_for_model(model)]
