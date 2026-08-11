"""Per-run context so the schedule tools reach the durable SchedulerStore."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class SchedulerCtx:
    store: Any  # SchedulerStore (duck-typed)
    agent_name: str = "shadow"


_active: ContextVar[SchedulerCtx | None] = ContextVar("madras_scheduler_ctx", default=None)


def set_scheduler_ctx(ctx: SchedulerCtx | None) -> None:
    _active.set(ctx)


def get_scheduler_ctx() -> SchedulerCtx | None:
    return _active.get()
