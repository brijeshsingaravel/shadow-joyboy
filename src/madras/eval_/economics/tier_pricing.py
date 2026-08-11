"""Tiered pricing — map each commercial tier to its model(s), compute cost-to-serve
from the measured/imputed price table, and solve price/margin per tier.

Tiers (from the product model): free = good OSS pay-as-you-go (we eat it, capped);
byok = user's key (we charge for governance, model cost = 0); subscriptions are
distinguished by the frontier model behind them (starter/pro/max).
"""

from __future__ import annotations

from typing import Any

from madras.eval_.economics.price_table import basis_of, cost_of_tokens

# tier -> {models, note}. The first model is the default for costing.
TIER_MODELS: dict[str, dict[str, Any]] = {
    "free": {"models": ["llama-70b", "qwen3.5", "glm-5.1"], "note": "OSS pay-as-you-go"},
    "byok": {"models": ["user"], "note": "user's own key — model cost is theirs"},
    "starter": {"models": ["claude-haiku", "gemini-flash"], "note": "cheap frontier"},
    "pro": {"models": ["claude-sonnet", "gpt-4o"], "note": "mid frontier"},
    "max": {"models": ["claude-opus", "gpt-5"], "note": "top frontier"},
}

# Light default infra cost per user-month (USD) — overlaid on model cost.
DEFAULT_INFRA_USD = 0.30


def cost_to_serve_tier(
    tier: str,
    *,
    model: str,
    tokens_per_task: tuple[int, int],
    tasks_per_month: int,
    infra_usd: float = DEFAULT_INFRA_USD,
    cached_frac: float = 0.0,
    batch: bool = False,
) -> dict[str, Any]:
    """Cost to serve one user for a month on this tier+model.

    ``cached_frac`` applies prompt-cache savings (cached input at 10%) — the single
    biggest lever for agentic workloads that re-send a static prefix each step.
    """
    in_tok, out_tok = tokens_per_task
    if tier == "byok" or model == "user":
        model_cost = 0.0
        basis = "byok"
    else:
        per_task = cost_of_tokens(model, in_tok, out_tok, cached_frac=cached_frac, batch=batch)
        model_cost = per_task * tasks_per_month
        basis = basis_of(model)
    return {
        "tier": tier,
        "model": model,
        "basis": basis,
        "cached_frac": round(cached_frac, 2),
        "model_cost_usd": round(model_cost, 4),
        "infra_usd": round(infra_usd, 4),
        "cogs_usd": round(model_cost + infra_usd, 4),
    }


def madras_tier_economics(
    *,
    tasks_per_month: int,
    tokens_per_task: tuple[int, int],
    cached_frac: float = 0.0,
    ppp: bool = False,
    infra_usd: float = DEFAULT_INFRA_USD,
) -> list[dict[str, Any]]:
    """The canonical §15 tier table: per-tier COGS (caching-aware) at the tier's
    DEFAULT model, the §15 price, the resulting margin, and the credit allowance.
    Fulfils decisions #1 (§15 prices), #2 (credits), #3 (INTERNAL-default), #4 (caching).
    """
    from madras.eval_.economics.tiers import MADRAS_TIERS, TIER_TASK_CAP, tier_default_model

    rows: list[dict[str, Any]] = []
    for tier, cfg in MADRAS_TIERS.items():
        price = cfg["price_ppp"] if ppp else cfg["price_usd"]
        model = tier_default_model(tier)
        # Cost at the tier's real monthly task CAP (research-grounded); fall back to the
        # passed default for uncapped tiers (byok/enterprise).
        cap = TIER_TASK_CAP.get(tier)
        n_tasks = cap if cap is not None else tasks_per_month
        c = cost_to_serve_tier(
            tier,
            model=model,
            tokens_per_task=tokens_per_task,
            tasks_per_month=n_tasks,
            infra_usd=infra_usd,
            cached_frac=cached_frac,
        )
        c["tasks_capped_at"] = cap
        margin = (
            round(1.0 - c["cogs_usd"] / price, 4)
            if isinstance(price, (int, float)) and price
            else None
        )
        rows.append(
            {
                **c,
                "model_class": cfg["model_class"],
                "price": price,
                "margin_at_price": margin,
                "credits": cfg["credits"],
                "note": cfg["note"],
            }
        )
    return rows


def solve_tier_price(*, cogs_usd: float, target_margin: float) -> float:
    """price = cogs / (1 - margin). margin 0 => price == cogs (free/at-cost)."""
    m = max(0.0, min(0.99, float(target_margin)))
    return round(cogs_usd / (1.0 - m), 4) if m > 0 else round(cogs_usd, 4)


def build_tier_table(
    *,
    tokens_per_task: tuple[int, int],
    tasks_per_month: int,
    prices: dict[str, float],
    margins: dict[str, float],
    infra_usd: float = DEFAULT_INFRA_USD,
) -> list[dict[str, Any]]:
    """One row per tier: COGS, the recommended price (from margin), and the actual
    margin at the operator-set price (if provided)."""
    rows: list[dict[str, Any]] = []
    for tier, spec in TIER_MODELS.items():
        model = spec["models"][0]
        c = cost_to_serve_tier(
            tier,
            model=model,
            tokens_per_task=tokens_per_task,
            tasks_per_month=tasks_per_month,
            infra_usd=infra_usd,
        )
        margin = float(margins.get(tier, 0.0))
        rec_price = solve_tier_price(cogs_usd=c["cogs_usd"], target_margin=margin)
        set_price = prices.get(tier)
        margin_at_price = round(1.0 - c["cogs_usd"] / set_price, 4) if set_price else None
        rows.append(
            {
                **c,
                "target_margin": margin,
                "recommended_price": rec_price,
                "price": set_price,
                "margin_at_price": margin_at_price,
                "note": spec["note"],
            }
        )
    return rows
