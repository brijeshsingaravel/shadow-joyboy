# src/madras/eval_/economics/defaults.py
from __future__ import annotations

from madras.eval_.economics.models import InfraCostModel, UsageProfile

# ILLUSTRATIVE assumptions — SP8 replaces with measured cloud cost.
DEFAULT_INFRA = InfraCostModel(
    compute_usd_per_hour=0.04,
    db_usd_per_gb_month=0.10,
    redis_usd_per_gb_month=0.10,
    qdrant_usd_per_gb_month=0.10,
    sandbox_usd_per_task=0.0,
    memory_manager_usd_per_active_user_night=0.10,
    storage_usd_per_user_month=0.02,
    storage_gb_per_user=0.5,
    bandwidth_usd_per_gb=0.09,
    bandwidth_gb_per_user_month=1.0,
)
DEFAULT_PROFILES = {
    "free": UsageProfile(
        tier="free",
        label="typical",
        requests_per_day=8,
        tokens_per_request=1500,
        frontier_fraction=0.0,
        active_nights_per_month=20,
        compute_hours_per_user_month=0.4,
    ),
    "paid": UsageProfile(
        tier="paid",
        label="typical",
        requests_per_day=25,
        tokens_per_request=3000,
        frontier_fraction=1.0,
        active_nights_per_month=22,
        compute_hours_per_user_month=1.0,
    ),
    "byok": UsageProfile(
        tier="byok",
        label="typical",
        requests_per_day=20,
        tokens_per_request=3000,
        frontier_fraction=1.0,
        active_nights_per_month=20,
        compute_hours_per_user_month=0.8,
    ),
}
DEFAULT_USER_MIX = {"free": 0.95, "paid": 0.045, "byok": 0.005}
DEFAULT_TARGET_MARGINS = {"free": 0.0, "paid": 0.6, "byok": 0.5}
