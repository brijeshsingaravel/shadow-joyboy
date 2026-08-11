"""Per-run context so background_dispatch/background_check reach the durable job store."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class BackgroundJobCtx:
    store: Any  # BackgroundJobStore (duck-typed to avoid import cycle)
    session_id: str = ""
    agent_name: str = "shadow"


_active: ContextVar[BackgroundJobCtx | None] = ContextVar("madras_background_job_ctx", default=None)


def set_background_job_ctx(ctx: BackgroundJobCtx | None) -> None:
    _active.set(ctx)


def get_background_job_ctx() -> BackgroundJobCtx | None:
    return _active.get()
