"""EchoMemoryProvider — the T5.2 worked example proving the `ProviderRegistry` path end-to-end.

The simplest possible real `MemoryProvider`: an in-memory list, no external infra. Exists to
prove a third-party plugin author can actually implement the ABC, register through
`ProviderRegistry`, and be resolved by (kind, name) without the registry or core ever importing
this module directly -- the "plugins never touch core" contract (RFC-0002 §12.1's plugin-API
activation gap, closed).
"""

from __future__ import annotations

from typing import Any

from madras.plugins.providers import MemoryProvider


class EchoMemoryProvider(MemoryProvider):
    """Remembers everything it's told, recalls by substring match. Not for production use --
    the point is to be small enough to read in one sitting and prove the registration path."""

    name = "echo_memory"

    def __init__(self) -> None:
        self._items: list[Any] = []

    async def remember(self, item: Any) -> None:
        self._items.append(item)

    async def recall(self, query: str, *, limit: int = 5) -> list[Any]:
        matches = [i for i in self._items if query.lower() in str(i).lower()]
        return matches[:limit]


__all__ = ["EchoMemoryProvider"]
