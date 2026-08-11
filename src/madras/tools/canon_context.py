"""Per-run Canon Plan context for the canon_write/pivot/check tools."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class CanonCtx:
    ledger: Any  # CanonLedger or duck-typed fake
    project: str = "default"
    session_id: str | None = None


_active: ContextVar[CanonCtx | None] = ContextVar("madras_canon_ctx", default=None)


def set_canon_ctx(ctx: CanonCtx | None) -> None:
    _active.set(ctx)


def get_canon_ctx() -> CanonCtx | None:
    return _active.get()
