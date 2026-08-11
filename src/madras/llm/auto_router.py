"""Auto-router — best-model-per-prompt with one cost<->quality dial (row 97).

OpenRouter's `openrouter/auto` (NotDiamond) picks the best model per prompt. We can't run a trained
router (zero-cost + no paid calls), so this is a transparent HEURISTIC over the row-93 catalog:
score each candidate by a quality proxy (context window + capability flags) vs its cost, blended
by ONE knob `tradeoff` (0 = best quality, 10 = cheapest; default 7), with `allowed_models` wildcards
and an optional row-96 capability gate. The richer quality signal (the Proving-Ground leaderboard)
can replace `_quality` later. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatch

from madras.llm.capability_routing import CapabilityReq, route_capable
from madras.llm.model_catalog import ModelInfo


def _quality(model: ModelInfo) -> float:
    """A 0..1 capability proxy (weights sum to 1.0 when every signal is present)."""
    ctx = min(model.context_window / 200_000, 1.0) * 0.40
    reasoning = 0.25 if model.reasoning else 0.0
    tools = 0.20 if model.tool_call else 0.0
    structured = 0.15 if model.structured_output else 0.0
    return ctx + reasoning + tools + structured


def _allowed(model: ModelInfo, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(model.id, p) for p in patterns)


@dataclass
class Scored:
    model: ModelInfo
    score: float


@dataclass
class AutoRouteResult:
    model: ModelInfo | None = None
    ranked: list[Scored] = field(default_factory=list[Scored])


def auto_route(
    candidates: list[ModelInfo],
    *,
    tradeoff: int = 7,
    allowed: tuple[str, ...] = ("*",),
    require: CapabilityReq | None = None,
) -> AutoRouteResult:
    """Pick the best model for a prompt. `tradeoff` 0=best-quality .. 10=cheapest. Gates on
    `require` (row 96) + `allowed` wildcards, then blends quality vs cost by the dial."""
    pool = list(candidates)
    if require is not None:
        pool = route_capable(require, pool)
    pool = [m for m in pool if _allowed(m, allowed)]
    if not pool:
        return AutoRouteResult()

    weight = max(0, min(tradeoff, 10)) / 10.0  # 0 -> all quality, 1 -> all cheapness
    max_cost = max((m.input_cost for m in pool), default=0.0) or 1.0
    scored = [
        Scored(m, _quality(m) * (1.0 - weight) - (m.input_cost / max_cost) * weight) for m in pool
    ]
    scored.sort(key=lambda s: s.score, reverse=True)
    return AutoRouteResult(model=scored[0].model, ranked=scored)
