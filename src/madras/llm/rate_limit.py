"""Per-model request-rate limiter for the free-tier LiteLLM proxy.

Free tiers cap requests-per-minute per provider (NVIDIA NIM ~40, Cerebras ~30,
Gemini Flash ~10). The Proving Ground sweep fans out (agent + 5 judges, bounded
concurrency) and would otherwise burst straight past those caps and get 429'd
mid-run. ``RateLimiter`` paces calls per model id to a smooth 1-per-interval, and
``RateLimitedBackend`` wraps any ``LLMBackend`` so every ``complete`` waits its
turn first. Pure asyncio; ``clock``/``sleep`` are injectable for tests.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from madras.llm.gateway import LLMBackend, LLMRequest, LLMResponse

# Conservative per-model RPM (under the real free-tier caps, with margin).
DEFAULT_RPM = 30  # NVIDIA NIM ~40
PER_MODEL_RPM: dict[str, int] = {
    "gemini-flash": 8,  # Gemini Flash free ~10 RPM
    "gemini-pro": 4,  # Gemini Pro free ~5 RPM
}


class RateLimiter:
    """Smooth per-key rate limiter (1 request per 60/rpm seconds, per key)."""

    def __init__(
        self,
        *,
        default_rpm: int = DEFAULT_RPM,
        per_key_rpm: dict[str, int] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._default_interval = 60.0 / default_rpm
        self._intervals = {k: 60.0 / v for k, v in (per_key_rpm or {}).items()}
        self._next: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._clock = clock
        self._sleep = sleep

    def _lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    async def acquire(self, key: str) -> None:
        """Block until a request for ``key`` is allowed under its rate."""
        interval = self._intervals.get(key, self._default_interval)
        async with self._lock(key):
            now = self._clock()
            nxt = self._next.get(key, now)
            wait = max(0.0, nxt - now)
            # Reserve this slot; the next caller waits one more interval.
            self._next[key] = max(now, nxt) + interval
        if wait > 0:
            await self._sleep(wait)


class RateLimitedBackend(LLMBackend):
    """Wrap an ``LLMBackend`` so each ``complete`` is paced per model id."""

    def __init__(self, inner: LLMBackend, limiter: RateLimiter) -> None:
        self._inner = inner
        self._limiter = limiter

    async def complete(self, req: LLMRequest) -> LLMResponse:
        await self._limiter.acquire(req.model)
        return await self._inner.complete(req)


def make_proxy_rate_limiter() -> RateLimiter:
    """The default limiter for the shared LiteLLM proxy free tiers."""
    return RateLimiter(default_rpm=DEFAULT_RPM, per_key_rpm=PER_MODEL_RPM)
