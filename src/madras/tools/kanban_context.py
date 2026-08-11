"""Per-run kanban context so the kanban tools reach the durable board store."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class KanbanCtx:
    store: Any  # KanbanStore (duck-typed)
    n_workers: int = 2
    max_rounds: int = 20


_active: ContextVar[KanbanCtx | None] = ContextVar("madras_kanban_ctx", default=None)


def set_kanban_ctx(ctx: KanbanCtx | None) -> None:
    _active.set(ctx)


def get_kanban_ctx() -> KanbanCtx | None:
    return _active.get()
