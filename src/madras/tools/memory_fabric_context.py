"""Per-run context so the remember/recall tools reach the MemoryFabric store."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class MemoryFabricCtx:
    fabric: Any  # MemoryFabric (duck-typed to avoid import cycle)
    session_id: str = ""
    agent_name: str = "shadow"
    graph: Any = None  # RelationshipStore (L6), optional


_active: ContextVar[MemoryFabricCtx | None] = ContextVar("madras_memory_fabric", default=None)


def set_memory_fabric_ctx(ctx: MemoryFabricCtx | None) -> None:
    _active.set(ctx)


def get_memory_fabric_ctx() -> MemoryFabricCtx | None:
    return _active.get()
