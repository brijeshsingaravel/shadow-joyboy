"""Per-run messaging context — store + the (injectable) channel sender."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any


@dataclass
class MessagingCtx:
    store: Any  # MessageStore (duck-typed)
    session_id: str = ""
    agent_name: str = "shadow"
    sender: Any = None  # async (Message) -> bool ; default = Apprise dispatch
    schedule: Any = None  # E-E18: async (action: dict, run_at_ts: float) -> str|None
    tz: str = "UTC"  # E-E18: user's timezone for optimal-time scheduling


_active: ContextVar[MessagingCtx | None] = ContextVar("madras_messaging_ctx", default=None)


def set_messaging_ctx(ctx: MessagingCtx | None) -> None:
    _active.set(ctx)


def get_messaging_ctx() -> MessagingCtx | None:
    return _active.get()
