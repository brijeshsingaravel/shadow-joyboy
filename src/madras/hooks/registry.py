"""Hook registry + dispatch (W4·B5).

Register async callables per lifecycle event; ``dispatch`` runs them in order. For a
BLOCKING event (``pre_tool_use``) the first hook that returns ``allow=False`` blocks (its
message becomes the teach-back). For non-blocking events, hook messages are collected as
feedback. A hook that raises is swallowed (a buggy hook must never break the loop) — except
the deterministic-block contract is preserved.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any

from madras.hooks.models import BLOCKING_EVENTS, HOOK_EVENTS, HookResult

Hook = Callable[[str, dict[str, Any]], Awaitable[HookResult | None]]


class HookRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, list[Hook]] = defaultdict(list)

    def register(self, event: str, fn: Hook) -> Hook:
        if event not in HOOK_EVENTS:
            raise ValueError(f"unknown hook event {event!r}")
        self._hooks[event].append(fn)
        return fn

    def count(self, event: str) -> int:
        return len(self._hooks.get(event, []))

    async def dispatch(self, event: str, payload: dict[str, Any]) -> HookResult:
        """Run all hooks for ``event``. Block (BLOCKING_EVENTS) or collect feedback."""
        messages: list[str] = []
        for fn in self._hooks.get(event, []):
            try:
                r = await fn(event, payload)
            except Exception:
                continue  # a buggy hook never breaks the loop
            if r is None:
                continue
            if r.message:
                messages.append(r.message)
            if event in BLOCKING_EVENTS and not r.allow:
                return HookResult(allow=False, message=r.message or f"blocked by {event} hook")
        return HookResult(allow=True, message="; ".join(messages))
