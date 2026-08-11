"""Per-run context so the session_search tool can reach the Mind-Palace ledger."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class SessionSearchCtx:
    ledger: Any  # MindPalaceLedger (duck-typed)
    vector_index: Any = None
    project: str = "cockpit"


_active: ContextVar[SessionSearchCtx | None] = ContextVar("madras_session_search", default=None)


def set_session_search_ctx(ctx: SessionSearchCtx | None) -> None:
    _active.set(ctx)


def get_session_search_ctx() -> SessionSearchCtx | None:
    return _active.get()
