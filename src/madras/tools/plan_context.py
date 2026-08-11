"""Per-run active-plan context for the plan_write/plan_check tools."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class PlanCtx:
    # Any: PlanLedger or duck-typed fake — avoids a hard tools->mindpalace import edge
    ledger: Any
    session_id: str
    agent_name: str = "shadow"
    project: str = "default"
    current_plan_id: str | None = None  # mutable: set when a plan is written this run
    # Any: live Plan object (mindpalace.plan_ledger.Plan) — the in-memory source the
    # prompt renders from each turn, so the loop never reads the DB in the hot path.
    current_plan: Any = None


_active: ContextVar[PlanCtx | None] = ContextVar("madras_active_plan", default=None)


def set_active_plan(ctx: PlanCtx | None) -> None:
    _active.set(ctx)


def get_active_plan() -> PlanCtx | None:
    return _active.get()
