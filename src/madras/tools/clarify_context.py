"""Per-run clarify channel for the `clarify` tool.

Holds an async callback the cockpit supplies to put a structured question to the
user and await their answer (the channel the planning Analyst uses to propose
restructures). Mirrors the other per-run contexts (memory/plan/vision). When no
channel is active the tool degrades gracefully so headless runs don't hang.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass

# ask(question, options[, multi_select]) -> answer string. options = structured
# [{label, description}] or None (free-text). 2-arg callbacks still work (arity-detected
# by the clarify tool), so the richer signature is back-compatible.
AskFn = Callable[..., Awaitable[str]]


@dataclass
class ClarifyCtx:
    ask: AskFn


_active: ContextVar[ClarifyCtx | None] = ContextVar("madras_clarify_ctx", default=None)


def set_clarify_ctx(ctx: ClarifyCtx | None) -> None:
    _active.set(ctx)


def get_clarify_ctx() -> ClarifyCtx | None:
    return _active.get()
