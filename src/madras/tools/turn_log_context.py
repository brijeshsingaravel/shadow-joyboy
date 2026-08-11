"""Per-run context so the recall_turns tool can reach the per-turn log ledger (W1·c 3b)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class TurnLogCtx:
    ledger: Any  # TurnLogLedger (duck-typed)
    agent_name: str = "shadow"


_active: ContextVar[TurnLogCtx | None] = ContextVar("madras_turn_log", default=None)


def set_turn_log_ctx(ctx: TurnLogCtx | None) -> None:
    _active.set(ctx)


def get_turn_log_ctx() -> TurnLogCtx | None:
    return _active.get()
