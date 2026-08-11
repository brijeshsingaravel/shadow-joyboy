"""Leaderboard assembly — artificialanalysis-style rows + Pareto frontier.

Each row is one (agent x model x tier) unit with the headline columns: Madras Index
(capability), scaffold_lift (Shadow's value-add over the bare model), cost_of_pass
(USD per passed task), tokens/task and speed. The Pareto frontier keeps the units
that are not dominated on (higher index, lower cost) — the "worth shipping" set.
"""

from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.index import madras_index, scaffold_lift


def build_row(
    *,
    agent: str,
    model: str,
    tier: str,
    per_suite: dict[str, float],
    passed: int,
    total: int,
    total_cost: float,
    raw_index: float,
    in_tok: int,
    out_tok: int,
    wall_s: float,
) -> dict[str, Any]:
    idx = madras_index(per_suite, tier)
    tokens = int(in_tok) + int(out_tok)
    speed = round(out_tok / wall_s, 2) if wall_s > 0 else 0.0
    return {
        "agent": agent,
        "model": model,
        "tier": tier,
        "madras_index": idx,
        "raw_index": round(float(raw_index), 4),
        "scaffold_lift": scaffold_lift(idx, raw_index),
        "passed": int(passed),
        "total": int(total),
        "cost_of_pass": round(float(total_cost) / max(int(passed), 1), 6),
        "total_cost_usd": round(float(total_cost), 6),
        "tokens_per_task": tokens,
        "speed_tok_s": speed,
    }


def pareto_front(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Non-dominated on (max madras_index, min cost_of_pass)."""
    front: list[dict[str, Any]] = []
    for r in rows:
        dominated = any(
            o is not r
            and o["madras_index"] >= r["madras_index"]
            and o["cost_of_pass"] <= r["cost_of_pass"]
            and (o["madras_index"] > r["madras_index"] or o["cost_of_pass"] < r["cost_of_pass"])
            for o in rows
        )
        if not dominated:
            front.append(r)
    return front


def build_board(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ranked = sorted(rows, key=lambda r: (-r["madras_index"], r["cost_of_pass"]))
    return {"rows": ranked, "pareto": pareto_front(ranked)}


async def leaderboard_rows_from_store(
    store: Any,
    run_id: str,
    *,
    tier: str = "free",
    raw_index_by_model: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Build the board from a completed run's stored model_runs + cost rows.

    Reads per_benchmark (suite scores), aggregates cost+tokens per (agent,model) from
    pg_metrics, and assembles a leaderboard row per unit. raw_index_by_model (from a
    raw-baseline run) yields scaffold_lift; absent -> lift measured against 0.
    """
    raw_index_by_model = raw_index_by_model or {}
    model_runs = await store.leaderboard(run_id)
    cost_rows = await store.cost_rows(run_id)
    agg: dict[tuple[str, str], dict[str, float]] = {}
    for c in cost_rows:
        key = (c["agent"], c["model"])
        a = agg.setdefault(key, {"cost": 0.0, "tok": 0.0, "n": 0.0})
        a["cost"] += float(c.get("cost_usd") or 0.0)
        a["tok"] += float(c.get("tokens") or 0.0)
        a["n"] += 1.0
    rows: list[dict[str, Any]] = []
    for m in model_runs:
        key = (m["agent"], m["model"])
        a = agg.get(key, {"cost": float(m.get("cost_usd") or 0.0), "tok": 0.0, "n": 0.0})
        n = a["n"] or 1.0
        per_suite: dict[str, Any] = m.get("per_benchmark") or {}
        if isinstance(per_suite, str):
            import json

            per_suite = json.loads(per_suite or "{}")
        overall = float(m.get("overall") or 0.0)
        wall_per_task = (float(m.get("latency_ms") or 0.0) / 1000.0) / n or 1.0
        row = build_row(
            agent=m["agent"],
            model=m["model"],
            tier=tier,
            per_suite=per_suite,
            passed=round(overall * n),
            total=int(n),
            total_cost=a["cost"],
            raw_index=raw_index_by_model.get(m["model"], 0.0),
            in_tok=0,
            out_tok=int(a["tok"] / n),
            wall_s=wall_per_task,
        )
        # W0·3 outlier spine: surface the persisted compounding signature + verdict + pass^k
        row["pass_k"] = m.get("pass_k")
        row["compounding"] = m.get("compounding")
        row["is_outlier"] = m.get("is_outlier")
        rows.append(row)
    return build_board(rows)
