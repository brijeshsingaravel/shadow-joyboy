# src/madras/eval_/economics/report.py
from __future__ import annotations

from typing import Any

from madras.eval_.economics.cost_model import measured_cost
from madras.eval_.economics.models import (
    EconomicsReport,
    InfraCostModel,
    MechanismQuote,
    PricingRecommendation,
    ScalingReport,
    TierEconomics,
    UsageProfile,
)
from madras.eval_.economics.pricing import cost_to_serve, solve_pricing
from madras.eval_.economics.scaling import cost_delta_impact, scaling_sweep


async def run_economics(
    store: Any,
    *,
    run_id: str,
    infra: InfraCostModel,
    profiles: dict[str, UsageProfile],
    user_mix: dict[str, float],
    target_margins: dict[str, float],
    sensitivity_deltas: tuple[float, ...] = (0.05, 0.10),
    n_values: tuple[int, ...] = (1_000, 10_000, 100_000, 1_000_000),
    byok_user_spend: float = 10.0,
) -> EconomicsReport:
    by_tier_cost = await measured_cost(store, run_id=run_id)
    per_tier: dict[str, TierEconomics] = {}
    tier_reports: dict[str, tuple[Any, Any]] = {}
    measured_fields: list[str] = []
    assumed_fields: list[str] = ["infra", "usage_profiles", "user_mix"]

    for tier, usage in profiles.items():
        workloads = by_tier_cost.get(usage.tier, {})  # may be empty pre-launch -> assumed
        if not workloads:
            assumed_fields.append(f"{tier}:model_cost(no eval data)")
        else:
            measured_fields.append(f"{tier}:model_cost")
        cts = cost_to_serve(usage.tier, usage, workloads or _zero_workload(), infra)
        if usage.tier == "free":
            # The FREE tier is $0 to the user by definition — a pure cost center
            # cross-subsidized by paid. Pricing it at break-even would wrongly make
            # free users look like they pay their cost, inflating blended margin and
            # contradicting the cross-subsidy / min-conversion math (which both treat
            # free revenue as $0). So model it explicitly as a $0 loss leader.
            rec = _free_pricing()
        else:
            rec = solve_pricing(
                cts,
                target_margin=target_margins.get(tier, 0.5),
                usage=usage,
                byok_user_spend=byok_user_spend,
            )
        per_tier[tier] = TierEconomics(cost_to_serve=cts, pricing=rec)
        tier_reports[tier] = (cts, rec)

    blended: ScalingReport = scaling_sweep(tier_reports, user_mix, n_values=list(n_values))
    sensitivity = [
        cost_delta_impact(blended, improvement_usd_per_user=d) for d in sensitivity_deltas
    ]

    return EconomicsReport(
        source_run_id=run_id,
        per_tier=per_tier,
        blended=blended,
        sensitivity=sensitivity,
        provenance={"measured": measured_fields, "assumed": assumed_fields},
        generated_for={
            "tiers": list(profiles),
            "user_mix": user_mix,
            "target_margins": target_margins,
        },
    )


def _zero_workload() -> dict[str, Any]:
    from madras.eval_.economics.models import WorkloadCost

    return {"general": WorkloadCost(model_cost_usd_per_req=0.0, source="assumed-fallback")}


def _free_pricing() -> PricingRecommendation:
    """The free tier charges $0 (loss leader); revenue is 0 everywhere downstream."""
    return PricingRecommendation(
        tier="free",
        target_margin=0.0,
        quotes=[
            MechanismQuote(
                mechanism="subscription",
                break_even_price=0.0,
                target_margin_price=0.0,
                margin=None,
                note="free tier: $0 to user — cost cross-subsidized by paid",
            )
        ],
        recommended="subscription",
        rationale="Free tier is $0 by definition; a cost center cross-subsidized by paid.",
    )
