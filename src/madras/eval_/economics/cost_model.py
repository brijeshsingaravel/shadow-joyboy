from __future__ import annotations

from typing import Any, Protocol

from madras.eval_.economics.models import (
    Tier,
    WorkloadCost,
    tier_of_model,
    workload_of_benchmark,
)


class _CostSource(Protocol):
    async def cost_rows(self, run_id: str) -> list[dict[str, Any]]: ...


async def measured_cost(store: _CostSource, *, run_id: str) -> dict[Tier, dict[str, WorkloadCost]]:
    """Aggregate pg_metrics cost into per-(tier, workload) WorkloadCost."""
    rows = await store.cost_rows(run_id)
    # accumulate sums per (tier, workload)
    acc: dict[tuple[Tier, str], dict[str, float]] = {}
    for r in rows:
        tier = tier_of_model(str(r.get("model", "")))
        workload = workload_of_benchmark(r.get("benchmark_family"))
        key = (tier, workload)
        a = acc.setdefault(key, {"cost": 0.0, "tokens": 0.0, "n": 0.0})
        a["cost"] += float(r.get("cost_usd") or 0.0)
        a["tokens"] += float(r.get("tokens") or 0.0)
        a["n"] += 1.0
    out: dict[Tier, dict[str, WorkloadCost]] = {}
    for (tier, workload), a in acc.items():
        n = a["n"] or 1.0
        out.setdefault(tier, {})[workload] = WorkloadCost(
            model_cost_usd_per_req=round(a["cost"] / n, 8),
            tokens_in=int(a["tokens"] // 2),
            tokens_out=int(a["tokens"] - a["tokens"] // 2),
            n_requests_observed=int(a["n"]),
            source="measured",
        )
    return out
