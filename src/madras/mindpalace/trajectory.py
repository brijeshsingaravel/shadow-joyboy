"""Experience Harvesting's trajectory extraction (row experience-harvesting, the D41
Trajectory Lake bridge).

The raw material already exists (`audit/writer.py::AuditLogWriter.query` returns every
audit row for a session; the eval lab's DVC/MLflow/DuckDB infra -- a separate,
already-in-progress track -- handles storage/versioning/analytics). What's missing is
the EXTRACTION: structuring those raw rows into the note's own field schema
(goal/plan/tool-calls/failures/retries/output/cost/latency), consent-gated. Pure
function over already-fetched rows -- no new infra, no storage/versioning decisions,
so this doesn't collide with the parallel eval-lab track.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any, cast


@dataclass
class Trajectory:
    session_id: str
    agent_name: str
    consent: bool
    goal: str = ""
    steps: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    failures: int = 0
    retries: int = 0
    output: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0

    def to_row(self) -> dict[str, Any]:
        """Flat dict matching the eval lab's existing Parquet-export row shape
        (`scripts/duckdb_analytics.py --export`) -- no changes needed on that side."""
        return {
            "session_id": self.session_id,
            "agent_name": self.agent_name,
            "goal": self.goal,
            "steps": self.steps,
            "tool_call_count": len(self.tool_calls),
            "failures": self.failures,
            "retries": self.retries,
            "output": self.output,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
        }


def _empty(session_id: str, agent_name: str) -> Trajectory:
    return Trajectory(session_id=session_id, agent_name=agent_name, consent=False)


def extract_trajectory(
    records: list[dict[str, Any]],
    *,
    session_id: str,
    agent_name: str,
    consent: bool,
) -> Trajectory:
    """Structure raw `AuditLogWriter.query()` rows (ordered oldest -> newest) into a
    Trajectory. Consent-gated at the boundary: without consent, returns an empty,
    redacted Trajectory -- never a partial capture (deny-by-default, matching the
    note's own DSL intent: "governed, not per-agent opt-in")."""
    if not consent:
        return _empty(session_id, agent_name)
    if not records:
        return Trajectory(session_id=session_id, agent_name=agent_name, consent=True)

    goal = ""
    for r in records:
        signals: dict[str, Any] = r.get("signals") or {}
        candidate = signals.get("goal") or signals.get("task") or ""
        if candidate:
            goal = str(candidate)
            break

    tool_calls: list[dict[str, Any]] = []
    failures = 0
    tool_seq: list[str] = []
    total_cost = 0.0
    total_latency = 0.0
    output = ""

    for r in records:
        signals: dict[str, Any] = r.get("signals") or {}
        extras: dict[str, Any] = r.get("extras") or {}
        calls: list[Any] = r.get("tool_calls") or []
        if calls:
            tool_calls.extend(calls)
            for c in calls:
                if isinstance(c, dict):
                    c = cast("dict[str, Any]", c)
                    tool_seq.append(str(c.get("name", "")))
        if signals.get("task_completion") is False or extras.get("ok") is False:
            failures += 1
        total_cost += float(signals.get("cost_usd", 0.0) or 0.0)
        total_latency += float(signals.get("latency_ms", 0.0) or 0.0)
        text = extras.get("summary") or extras.get("content") or ""
        if text:
            output = str(text)

    # A retry is the same tool name appearing consecutively -- the agent re-tried it.
    retries = sum(1 for a, b in pairwise(tool_seq) if a == b)

    return Trajectory(
        session_id=session_id,
        agent_name=agent_name,
        consent=True,
        goal=goal,
        steps=len(records),
        tool_calls=tool_calls,
        failures=failures,
        retries=retries,
        output=output,
        cost_usd=round(total_cost, 6),
        latency_ms=round(total_latency, 3),
    )
