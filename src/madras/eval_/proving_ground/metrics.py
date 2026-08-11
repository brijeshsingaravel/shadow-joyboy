from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.suite import Case


def _errored(entry: dict[str, Any]) -> bool:
    if entry.get("ok") is False:
        return True
    return bool(entry.get("error"))


def compute_metrics(
    case: Case,
    trajectory: dict[str, Any],
    *,
    tokens: int = 0,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
) -> dict[str, float | None]:
    tools: list[dict[str, Any]] = list(trajectory.get("tools", []))
    n_tool_calls = len(tools)
    n_steps = trajectory.get("n_steps", n_tool_calls)

    if n_tool_calls:
        n_errored = sum(1 for t in tools if _errored(t))
        tool_error_rate = n_errored / n_tool_calls
    else:
        n_errored = 0
        tool_error_rate = 0.0

    recovery_rate: float | None
    if n_errored == 0:
        recovery_rate = None
    else:
        first_error_idx = next(i for i, t in enumerate(tools) if _errored(t))
        later_success = any(not _errored(t) for t in tools[first_error_idx + 1 :])
        recovery_rate = 1.0 if later_success else 0.0

    efficiency: float | None
    if case.optimal_steps is not None and n_steps > 0:
        efficiency = min(1.0, case.optimal_steps / n_steps)
    else:
        efficiency = None

    repair_used = 1.0 if any(t.get("repaired") for t in tools) else 0.0

    return {
        "n_steps": float(n_steps),
        "n_tool_calls": float(n_tool_calls),
        "tool_error_rate": tool_error_rate,
        "recovery_rate": recovery_rate,
        "efficiency": efficiency,
        "repair_used": repair_used,
        "latency_ms": float(latency_ms),
        "tokens": float(tokens),
        "cost_usd": float(cost_usd),
    }
