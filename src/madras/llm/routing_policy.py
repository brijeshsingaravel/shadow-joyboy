"""Declarative routing-preference policy (row 95).

Mirrors OpenRouter's `provider:{order, only, ignore, sort, max_price, allow_fallbacks}` over the
row-93 Model Catalog: a declarative `RoutingPolicy` selects + orders candidate models, producing the
ordered chain the row-94 fallback executor consumes (policy -> chain -> failover).
Zero-cost-aligned: `free_only` is first-class. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.llm.model_catalog import ModelInfo


@dataclass
class RoutingPolicy:
    only: tuple[str, ...] = ()  # provider allowlist (empty = all)
    ignore: tuple[str, ...] = ()  # provider denylist
    order: tuple[str, ...] = ()  # preferred provider priority (front-loaded)
    sort: str = ""  # "" | price | context
    max_price: float | None = None  # cap on input_cost (USD/token)
    free_only: bool = False  # zero-cost rule — free fleet only
    allow_fallbacks: bool = True  # False -> keep only the single best candidate


def apply_policy(policy: RoutingPolicy, candidates: list[ModelInfo]) -> list[ModelInfo]:
    """Filter -> sort -> order a candidate list per the policy. Output is the routing chain."""
    out = list(candidates)

    # 1. filter
    if policy.free_only:
        out = [m for m in out if m.free]
    if policy.only:
        out = [m for m in out if m.provider in policy.only]
    if policy.ignore:
        out = [m for m in out if m.provider not in policy.ignore]
    if policy.max_price is not None:
        out = [m for m in out if m.input_cost <= policy.max_price]

    # 2. sort (stable, so a later provider-order pass keeps this as the within-group tiebreak)
    if policy.sort == "price":
        out.sort(key=lambda m: (m.input_cost, m.output_cost))
    elif policy.sort == "context":
        out.sort(key=lambda m: -m.context_window)

    # 3. provider order — front-load preferred providers (unlisted providers go last, stable)
    if policy.order:
        rank = {provider: i for i, provider in enumerate(policy.order)}
        out.sort(key=lambda m: rank.get(m.provider, len(rank)))

    # 4. no fallbacks -> the single best candidate only
    if not policy.allow_fallbacks and out:
        out = out[:1]
    return out


def policy_chain(policy: RoutingPolicy, candidates: list[ModelInfo]) -> list[str]:
    """Convenience: the ordered list of model ids the row-94 fallback chain consumes."""
    return [m.id for m in apply_policy(policy, candidates)]
