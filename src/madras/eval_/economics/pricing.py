from __future__ import annotations

from madras.eval_.economics.models import (
    CostToServe,
    InfraCostModel,
    MechanismQuote,
    PricingRecommendation,
    Tier,
    UsageProfile,
    WorkloadCost,
)


def cost_to_serve(
    tier: Tier,
    usage: UsageProfile,
    workloads: dict[str, WorkloadCost],
    infra: InfraCostModel,
) -> CostToServe:
    """$/user/month = model (measured, requests x per-req cost) + infra + memory."""
    per_req = sum(w.model_cost_usd_per_req for w in workloads.values()) / max(len(workloads), 1)
    model = per_req * usage.requests_per_month * usage.frontier_fraction
    infra_amt = infra.amortized_per_user_month(usage)
    memory = infra.memory_manager_usd_per_active_user_night * usage.active_nights_per_month
    infra_only = round(infra_amt - memory, 6)  # amortized already includes memory; split it out
    total = round(model + infra_only + memory, 6)
    measured = [k for k, w in workloads.items() if w.source == "measured"]
    assumed = ["infra", "memory", "usage"] + [
        k for k, w in workloads.items() if w.source != "measured"
    ]
    return CostToServe(
        tier=tier,
        usd_per_user_month=total,
        breakdown={"model": round(model, 6), "infra": infra_only, "memory": round(memory, 6)},
        provenance={"measured": measured, "assumed": assumed},
    )


def _validate_margin(target_margin: float) -> None:
    if not (0.0 <= target_margin < 1.0):
        raise ValueError(f"target_margin must be in [0,1): got {target_margin}")


def solve_pricing(
    cost: CostToServe,
    *,
    target_margin: float,
    usage: UsageProfile,
    byok_user_spend: float = 0.0,
    byok_fee: float = 0.15,
    predictability_weight: float = 0.15,
) -> PricingRecommendation:
    """Solve break-even + target-margin price for each mechanism; recommend one."""
    _validate_margin(target_margin)
    c = cost.usd_per_user_month
    quotes: list[MechanismQuote] = []

    # subscription: flat price covering cost at target margin
    sub_price = round(c / (1.0 - target_margin), 6)
    quotes.append(
        MechanismQuote(
            mechanism="subscription",
            break_even_price=round(c, 6),
            target_margin_price=sub_price,
            margin=target_margin,
            note="flat $/user/mo, fair-use cap = typical usage",
        )
    )

    # usage-metered: markup on cost; same realized margin, less revenue floor
    markup = round(1.0 / (1.0 - target_margin), 6)
    quotes.append(
        MechanismQuote(
            mechanism="usage",
            markup_multiplier=markup,
            margin=target_margin,
            note="charge cost x markup per request; revenue tracks usage (no floor)",
        )
    )

    # hybrid: subscription base at margin + overage at the usage markup
    quotes.append(
        MechanismQuote(
            mechanism="hybrid",
            break_even_price=round(c, 6),
            target_margin_price=sub_price,
            markup_multiplier=markup,
            margin=target_margin,
            note="base covers typical usage at margin; overage at usage markup",
        )
    )

    # byok: user pays the model; platform earns a fee, bears only infra
    if cost.tier == "byok":
        fee = byok_fee * byok_user_spend
        infra_cost = cost.breakdown["infra"] + cost.breakdown["memory"]
        byok_margin = round((fee - infra_cost) / fee, 6) if fee > 0 else None
        quotes.append(
            MechanismQuote(
                mechanism="byok",
                margin=byok_margin,
                note=f"platform fee {byok_fee:.0%} - infra; user pays model provider directly",
            )
        )
        return PricingRecommendation(
            tier=cost.tier,
            target_margin=target_margin,
            quotes=quotes,
            recommended="byok",
            rationale="BYOK tier monetizes via the platform fee, not a price.",
        )

    # selection: score = margin + predictability bonus (sub/hybrid have a revenue floor)
    def score(q: MechanismQuote) -> float:
        floor_bonus = predictability_weight if q.mechanism in ("subscription", "hybrid") else 0.0
        return (q.margin or 0.0) + floor_bonus

    best = max((q for q in quotes if q.mechanism != "byok"), key=score)
    rationale = (
        f"{best.mechanism} maximizes margin plus a predictability bonus "
        f"(floor bonus {predictability_weight} for revenue stability)."
    )
    return PricingRecommendation(
        tier=cost.tier,
        target_margin=target_margin,
        quotes=quotes,
        recommended=best.mechanism,
        rationale=rationale,
    )
