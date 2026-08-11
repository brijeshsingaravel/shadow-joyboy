"""Build the per-action signal dict from a turn's outcome.

This is the canonical place where the contract from BASE_AGENT_SCHEMA.md §5
maps onto runtime values.
"""

from __future__ import annotations

from typing import Any

REQUIRED_PER_ACTION = [
    "task_completion",
    "trajectory_trace",
    "tool_calls",
    "tool_selection",
    "argument_correctness",
    "confidence",
    "latency_ms",
    "cost_usd",
]


class IncompleteSignalEmission(Exception):
    """Raised when a per-action emission misses a required signal."""


def emit_action_signals(raw: dict[str, Any]) -> dict[str, Any]:
    """Validate and return the per-action signals. Missing required → raise."""
    missing = [k for k in REQUIRED_PER_ACTION if k not in raw]
    if missing:
        raise IncompleteSignalEmission(f"missing required per-action signals: {missing}")
    return {k: raw[k] for k in REQUIRED_PER_ACTION}
