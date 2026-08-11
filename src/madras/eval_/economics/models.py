from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Tier = Literal["free", "paid", "byok"]
Mechanism = Literal["subscription", "usage", "hybrid", "byok"]

# Model -> tier. FREE-OSS models run the free tier; everything else is paid
# (frontier-harnessed). Unknown models default to "paid" so we never under-price.
MODEL_TIER: dict[str, Tier] = {
    "llama-70b": "free",
    "qwen3": "free",
    "qwen3-32b": "free",
    "qwen-coder": "free",
    "deepseek-r1": "free",
    "qwq": "free",
    "gemini-flash": "free",
    # paid / frontier-harnessed
    "nemotron-super-120b": "paid",
    "qwen3.5": "paid",
    "glm-5.1": "paid",
    "kimi-k2": "paid",
    "gpt-oss-120b": "paid",
    "gemini-pro": "paid",
    "step-3.7-flash": "paid",
}

# benchmark_family -> coarse workload class (drives per-workload cost rollups).
BENCHMARK_WORKLOAD: dict[str, str] = {
    "swebench": "code",
    "terminal_bench": "code",
    "bfcl": "tool",
    "tau2": "tool",
    "gsm8k": "reasoning",
    "gpqa": "reasoning",
    "mmlu_pro": "reasoning",
    "gaia": "research",
    "agentharm": "safety",
}


def tier_of_model(model: str) -> Tier:
    return MODEL_TIER.get(model, "paid")


def workload_of_benchmark(benchmark_family: str | None) -> str:
    if not benchmark_family:
        return "general"
    return BENCHMARK_WORKLOAD.get(benchmark_family, "general")


class WorkloadCost(BaseModel):
    """Measured per-request cost for one (tier, workload) class."""

    model_cost_usd_per_req: float
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float | None = None
    n_requests_observed: int = 0
    source: Literal["measured", "assumed-fallback"] = "measured"


class InfraCostModel(BaseModel):
    """Parameterized infra cost. Every field is a constructor parameter with a
    default; SP8 later replaces these with measured cloud cost."""

    compute_usd_per_hour: float = 0.0
    db_usd_per_gb_month: float = 0.0
    redis_usd_per_gb_month: float = 0.0
    qdrant_usd_per_gb_month: float = 0.0
    sandbox_usd_per_task: float = 0.0
    memory_manager_usd_per_active_user_night: float = 0.10  # Blueprint §11 midpoint
    storage_usd_per_user_month: float = 0.0
    bandwidth_usd_per_gb: float = 0.0
    storage_gb_per_user: float = 0.0
    bandwidth_gb_per_user_month: float = 0.0

    def amortized_per_user_month(self, usage: UsageProfile) -> float:
        compute = self.compute_usd_per_hour * usage.compute_hours_per_user_month
        memory = self.memory_manager_usd_per_active_user_night * usage.active_nights_per_month
        storage = self.storage_usd_per_user_month + self.storage_usd_per_gb_month_total()
        bandwidth = self.bandwidth_usd_per_gb * self.bandwidth_gb_per_user_month
        return round(compute + memory + storage + bandwidth, 6)

    def storage_usd_per_gb_month_total(self) -> float:
        per_gb = (
            self.db_usd_per_gb_month + self.redis_usd_per_gb_month + self.qdrant_usd_per_gb_month
        )
        return per_gb * self.storage_gb_per_user


class UsageProfile(BaseModel):
    tier: Tier
    label: Literal["light", "typical", "power"]
    requests_per_day: float
    tokens_per_request: float
    sessions_per_month: float = 30.0
    frontier_fraction: float = Field(default=1.0, ge=0.0, le=1.0)
    active_nights_per_month: float = 20.0
    compute_hours_per_user_month: float = 1.0

    @property
    def requests_per_month(self) -> float:
        return self.requests_per_day * 30.0


class CostToServe(BaseModel):
    tier: Tier
    usd_per_user_month: float
    breakdown: dict[str, float]  # {"model","infra","memory"}
    provenance: dict[str, list[str]]  # {"measured":[...],"assumed":[...]}


class MechanismQuote(BaseModel):
    mechanism: Mechanism
    break_even_price: float | None = None  # $/user/mo (sub/hybrid)
    target_margin_price: float | None = None  # $/user/mo (sub/hybrid)
    markup_multiplier: float | None = None  # usage-metered
    margin: float | None = None  # realized margin at the recommendation
    note: str = ""


class PricingRecommendation(BaseModel):
    tier: Tier
    target_margin: float
    quotes: list[MechanismQuote]
    recommended: Mechanism
    rationale: str


class SensitivityDelta(BaseModel):
    improvement_usd_per_user: float
    margin_drop: float  # absolute blended-margin drop
    price_bump_to_hold: float  # $/paid-user/mo bump needed to hold blended margin


class ScalingReport(BaseModel):
    user_mix: dict[str, float]
    margin_by_n: dict[str, float]  # blended margin at each N
    blended_revenue_per_user: float
    blended_cost_per_user: float
    cross_subsidy_per_user: float  # paid+byok revenue - free cost (per blended user)
    min_paid_conversion_for_breakeven: float | None
    where_it_breaks: str | None  # None = stable across the swept range


class TierEconomics(BaseModel):
    cost_to_serve: CostToServe
    pricing: PricingRecommendation


class EconomicsReport(BaseModel):
    source_run_id: str
    per_tier: dict[str, TierEconomics]
    blended: ScalingReport
    sensitivity: list[SensitivityDelta]
    provenance: dict[str, list[str]]
    generated_for: dict[str, object]
