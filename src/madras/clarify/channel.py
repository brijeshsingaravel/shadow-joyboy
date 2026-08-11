"""Live clarify channel — pause the loop on a question, resume on the user's answer.

The clarify tool's ask() awaits a per-session asyncio.Future; the cockpit (or any
client) resolves it by POSTing the answer. A timeout degrades gracefully so a loop never
hangs forever if the user walks away. A module-level registry lets the loop and the HTTP
endpoint share the same pending question.
"""

from __future__ import annotations

import asyncio
from typing import Any


class ClarifyChannel:
    """Per-session pending-question registry backed by asyncio Futures."""

    def __init__(self) -> None:
        self._pending: dict[str, tuple[dict[str, Any], asyncio.Future[str]]] = {}

    async def ask(
        self,
        session_id: str,
        question: str,
        options: list[Any] | None,
        multi_select: bool = False,
        *,
        timeout: float = 300.0,
    ) -> str:
        """Register the question and await the user's answer (or '' on timeout).
        `options` are structured ({label, description}); `multi_select` lets the UI render
        checkboxes + previews. Stored in pending so the cockpit can render them."""
        fut: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        self._pending[session_id] = (
            {"question": question, "options": options, "multi_select": multi_select},
            fut,
        )
        try:
            return await asyncio.wait_for(fut, timeout)
        except (TimeoutError, asyncio.CancelledError):
            return ""
        finally:
            self._pending.pop(session_id, None)

    def answer(self, session_id: str, text: str) -> bool:
        """Resolve the pending question for a session. Returns False if none pending."""
        item = self._pending.get(session_id)
        if item is None or item[1].done():
            return False
        item[1].set_result(text)
        return True

    def pending(self, session_id: str) -> dict[str, Any] | None:
        """The currently-pending question for a session (for the UI to render), or None."""
        item = self._pending.get(session_id)
        return dict(item[0]) if item is not None else None


# Process-wide channel shared by the cockpit loop + the /clarify endpoints.
CHANNEL = ClarifyChannel()
