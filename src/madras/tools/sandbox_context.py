"""Per-run active-sandbox context for dangerous tools."""

from __future__ import annotations

from contextvars import ContextVar

from madras.tools.sandbox import Sandbox

_active: ContextVar[Sandbox | None] = ContextVar("madras_active_sandbox", default=None)


def set_active_sandbox(sb: Sandbox | None) -> None:
    _active.set(sb)


def get_active_sandbox() -> Sandbox | None:
    return _active.get()
