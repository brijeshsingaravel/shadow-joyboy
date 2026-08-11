"""Per-run active-memory context for the note tool."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryCtx:
    # Any: EpisodicMemory or duck-typed fake — avoids circular import between tools and memory
    episodic: Any
    session_id: str
    agent_name: str = "shadow"


_active: ContextVar[MemoryCtx | None] = ContextVar("madras_active_memory", default=None)


def set_active_memory(ctx: MemoryCtx | None) -> None:
    _active.set(ctx)


def get_active_memory() -> MemoryCtx | None:
    return _active.get()
