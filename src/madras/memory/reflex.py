"""L4 Reflex memory — Redis permanent namespace. Phase 0 = read/write only.

Reflex FORMATION (the Muscle Memory pattern detector) is a Phase 1 task
in the Memory Manager. Phase 0 only needs storage + lookup so the graph
can wire them.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import cast

import redis.asyncio as redis_asyncio


@dataclass
class ReflexCandidate:
    task_shape_hash: str
    tool_sequence: list[str]
    success_count: int
    success_rate: float


class ReflexMemory:
    def __init__(self, *, redis_url: str) -> None:
        self._client = redis_asyncio.from_url(redis_url, decode_responses=True)

    def _key(self, agent: str, shape_hash: str) -> str:
        return f"reflex:{agent}:{shape_hash}"

    async def write_candidate(self, agent: str, candidate: ReflexCandidate) -> None:
        # Permanent — no TTL. Decay happens by score, not by expiry.
        await self._client.set(
            self._key(agent, candidate.task_shape_hash),
            json.dumps(asdict(candidate)),
        )

    async def lookup_by_shape(self, agent: str, shape_hash: str) -> ReflexCandidate | None:
        raw = await self._client.get(self._key(agent, shape_hash))
        if raw is None:
            return None
        return ReflexCandidate(**json.loads(raw))

    async def all_for_agent(self, agent: str) -> list[ReflexCandidate]:
        """Every reflex held by `agent` (for mentor→mentee inheritance / introspection)."""
        out: list[ReflexCandidate] = []
        async for raw_key in self._client.scan_iter(  # type: ignore[reportUnknownMemberType]
            match=f"reflex:{agent}:*"
        ):
            key_str = cast("str", raw_key)
            raw = await self._client.get(key_str)
            if raw:
                out.append(ReflexCandidate(**json.loads(raw)))
        return out

    async def close(self) -> None:
        await self._client.aclose()
