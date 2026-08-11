"""select_model — the one seam that composes the routing modules (row 93-97) into the
ordered chain the fallback executor consumes. Nothing else in the codebase called these
together before this (s46 audit finding: routing_policy/auto_router/model_catalog/
task_router/fallback_chain each existed, tested, but never composed or wired into a real
turn).

`configured_model` (the role YAML's own choice) is always first in the returned chain --
routing supplies fallback alternatives, it never silently overrides an explicit choice.
"""

from __future__ import annotations

from madras.config import settings
from madras.llm.auto_router import auto_route
from madras.llm.model_catalog import ModelCatalog
from madras.llm.routing_policy import RoutingPolicy, apply_policy


def select_model(
    *,
    configured_model: str,
    catalog: ModelCatalog | None = None,
    policy: RoutingPolicy | None = None,
    tradeoff: int = 7,
) -> list[str]:
    """Return the ordered model chain for `run_with_fallback_async`: `configured_model`
    first, then the policy-filtered, auto-router-scored pool as fallback alternatives.

    `policy` defaults to `RoutingPolicy(free_only=settings.llm_free_only)` -- the single
    test-vs-launch switch (s46). `catalog` defaults to the offline zero-cost seed
    (`ModelCatalog.with_free_fleet()`) -- no live OpenRouter fetch, no API hammering;
    pass a `sync_openrouter()`-populated catalog for the full priced pool at launch.
    """
    policy = policy or RoutingPolicy(free_only=settings.llm_free_only)
    catalog = catalog or ModelCatalog.with_free_fleet()

    pool = apply_policy(policy, catalog.filter())
    ranked = auto_route(pool, tradeoff=tradeoff).ranked
    fallbacks = [s.model.id for s in ranked if s.model.id != configured_model]

    return [configured_model, *fallbacks]
