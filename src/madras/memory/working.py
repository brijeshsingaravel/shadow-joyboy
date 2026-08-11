"""Working memory (L1) — Redis TTL. Sub-5ms reads, per-tenant per-session namespaced."""

from __future__ import annotations

from typing import cast

import redis.asyncio as redis_asyncio


class WorkingMemory:
    """L1 adapter. Always-on, per-tenant per-session isolation."""

    def __init__(self, *, redis_url: str, ttl_seconds: int = 7200) -> None:
        self._client: redis_asyncio.Redis = redis_asyncio.from_url(redis_url, decode_responses=True)
        self._ttl = ttl_seconds

    def _key(self, namespace: str, slot: str) -> str:
        return f"wm:{namespace}:{slot}"

    async def write(self, namespace: str, slot: str, value: str) -> None:
        await self._client.set(self._key(namespace, slot), value, ex=self._ttl)

    async def read(self, namespace: str, slot: str) -> str | None:
        # decode_responses=True guarantees str (never bytes) on a real read; the redis-py
        # stubs return bytes | str | None because the client type is generic over both.
        return cast("str | None", await self._client.get(self._key(namespace, slot)))

    async def close(self) -> None:
        await self._client.aclose()
