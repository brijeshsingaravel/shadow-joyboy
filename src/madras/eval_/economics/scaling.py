from __future__ import annotations

from madras.eval_.economics.models import (
    CostToServe,
    PricingRecommendation,
    ScalingReport,
    SensitivityDelta,
)

TierReport = dict[str, tuple[CostToServe, PricingRecommendation]]


def _price_of(rec: PricingRecommendation) -> float:
    q = next((q for q in rec.quotes if q.mechanism == rec.recommended), None)
    if q is None:
        return 0.0
    return q.target_margin_price or 0.0


def _blended(tiers: TierReport, mix: dict[str, float]) -> tuple[float, float]:
    revenue = sum(mix.get(t, 0.0) * _price_of(rec) for t, (_c, rec) in tiers.items())
    cost = sum(mix.get(t, 0.0) * cts.usd_per_user_month for t, (cts, _r) in tiers.items())
    return round(revenue, 6), round(cost, 6)


def scaling_sweep(
    tiers: TierReport,
    user_mix: dict[str, float],
    *,
    n_values: list[int],
) -> ScalingReport:
    revenue, cost = _blended(tiers, user_mix)
    margin = 0.0 if revenue == 0 else round((revenue - cost) / revenue, 6)
    # blended margin is per-user and N-independent in this linear model; the sweep
    # exposes it per N so SP8 can later inject N-dependent infra (step costs).
    margin_by_n = {str(n): margin for n in n_values}

    # cross-subsidy: paid+byok revenue vs free cost (per blended user)
    free_cost = (
        user_mix.get("free", 0.0) * tiers["free"][0].usd_per_user_month if "free" in tiers else 0.0
    )
    paid_rev = sum(
        user_mix.get(t, 0.0) * _price_of(rec) for t, (_c, rec) in tiers.items() if t != "free"
    )
    cross = round(paid_rev - free_cost, 6)

    # min paid conversion f s.t. blended margin >= 0, holding free as the remainder.
    # Assume a single paid tier "paid"; f*price_paid >= f*cost_paid + (1-f)*cost_free
    min_conv: float | None = None
    if "paid" in tiers and "free" in tiers:
        price_p = _price_of(tiers["paid"][1])
        cost_p = tiers["paid"][0].usd_per_user_month
        cost_f = tiers["free"][0].usd_per_user_month
        denom = (price_p - cost_p) + cost_f
        min_conv = round(cost_f / denom, 6) if denom > 0 else None

    where = None if margin > 0 else f"blended margin <= 0 at mix {user_mix}"
    return ScalingReport(
        user_mix=user_mix,
        margin_by_n=margin_by_n,
        blended_revenue_per_user=revenue,
        blended_cost_per_user=cost,
        cross_subsidy_per_user=cross,
        min_paid_conversion_for_breakeven=min_conv,
        where_it_breaks=where,
    )


def cost_delta_impact(
    report: ScalingReport, *, improvement_usd_per_user: float
) -> SensitivityDelta:
    """A UX improvement adds blended cost/user -> margin drop + price bump to hold."""
    rev = report.blended_revenue_per_user
    new_cost = report.blended_cost_per_user + improvement_usd_per_user
    old_margin = 0.0 if rev == 0 else (rev - report.blended_cost_per_user) / rev
    new_margin = 0.0 if rev == 0 else (rev - new_cost) / rev
    drop = round(old_margin - new_margin, 6)
    # to hold old_margin: new_rev = new_cost/(1-old_margin); bump spread over the same blended user
    bump = 0.0
    if rev > 0 and old_margin < 1.0:
        needed_rev = new_cost / (1.0 - old_margin)
        bump = round(max(needed_rev - rev, 0.0), 6)
    return SensitivityDelta(
        improvement_usd_per_user=improvement_usd_per_user,
        margin_drop=drop,
        price_bump_to_hold=bump,
    )
