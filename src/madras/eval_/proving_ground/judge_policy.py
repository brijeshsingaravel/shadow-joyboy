"""P2 economics — judge routing.

The Madras Index can only refresh continuously if scoring is cheap. So routine
per-action scoring routes to ONE cheap (distilled) judge, while high-stakes GATE
events (promotion, demotion, release red-team, ASI audit, marketplace listing)
convene the full diverse panel under a supermajority (governance-eval.md §1 —
"routine_scoring: llm_as_judge" vs the "agent_as_judge_triggers"). The distilled
judge model plugs in here as `cheap_judge`; this module is just the routing.
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from madras.eval_.proving_ground.judge_panel import JUDGES_DEFAULT, PanelVerdict, judge_panel

# High-stakes events that warrant the full Agent-as-Judge panel.
GATE_EVENTS = frozenset(
    {
        "promotion_gate",
        "demotion_review",
        "red_team_release_gate",
        "asi_audit",
        "marketplace_listing",
    }
)

# The cheap distilled judge used for routine scoring (Galileo-Luna / Patronus-Lynx
# pattern). A small fast model; swap when the trained Madras judge ships.
DEFAULT_CHEAP_JUDGE = "gemini-flash"


@dataclass
class JudgePlan:
    judges: list[str]
    threshold: int  # number of judges that must pass
    mode: str  # "routine" | "gate"


def _supermajority(n: int) -> int:
    """≈80% of the panel must pass (5 -> 4, 3 -> 3)."""
    return max(1, math.ceil(0.8 * n))


def plan_judges(
    event: str,
    *,
    cheap_judge: str = DEFAULT_CHEAP_JUDGE,
    panel: list[str] | None = None,
) -> JudgePlan:
    """Route an eval `event` to either the cheap routine judge or the gate panel."""
    if event in GATE_EVENTS:
        full = list(panel) if panel is not None else list(JUDGES_DEFAULT)
        return JudgePlan(judges=full, threshold=_supermajority(len(full)), mode="gate")
    return JudgePlan(judges=[cheap_judge], threshold=1, mode="routine")


async def judge_for_event(
    event: str,
    rubric: str,
    task: str,
    trajectory: dict[str, Any],
    *,
    call: Callable[..., Awaitable[dict[str, Any]]],
    cheap_judge: str = DEFAULT_CHEAP_JUDGE,
    panel: list[str] | None = None,
    meta_call: Callable[..., Awaitable[dict[str, Any]]] | None = None,
    meta_judge: str | None = None,
) -> tuple[PanelVerdict, JudgePlan]:
    """Score with the routed judges: ONE cheap (distilled) judge for routine events,
    the full diverse panel under supermajority for gate events. Returns the verdict
    plus the plan used (so the caller can record mode/cost). The distilled judge model
    swaps in via `cheap_judge` — this is the routing, not the model.
    """
    plan = plan_judges(event, cheap_judge=cheap_judge, panel=panel)
    verdict = await judge_panel(
        rubric,
        task,
        trajectory,
        judges=plan.judges,
        call=call,
        threshold=plan.threshold,
        meta_call=meta_call,
        meta_judge=meta_judge,
    )
    return verdict, plan
