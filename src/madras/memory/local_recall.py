"""Zero-API local recall — MemPalace-style scoped memory (B69).

MemPalace's insight (mempalace/mempalace, MIT, zero-API, 96.6% R@5): store content **verbatim** and
index it **structurally** — people/projects become *wings*, topics become *rooms*, original content
lives in *drawers* — so recall is **SCOPED** (search one wing/room) rather than run against a flat
corpus. Higher precision, lower cost, no embedding API. This is BOTH a spine method (`LocalPalace`,
pure-Python over our retrieval core — zero infra/LLM) AND a capability backend (`MemPalaceBackend`,
injectable, plugs into the [[Memory Benchmark]] `MemoryBackend` interface for the A/B).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from madras.memory.benchmark import Turn
from madras.memory.retrieval import MemoryItem, recall


@dataclass
class LocalPalace:
    """Wing → room → verbatim items. Recall can be scoped to a wing and/or room (the MemPalace
    win) or palace-wide; ranking reuses the Fabric's pure relevance-x-recency core (zero-API)."""

    # wing -> room -> items (verbatim drawers)
    _index: dict[str, dict[str, list[MemoryItem]]] = field(
        default_factory=dict[str, dict[str, list[MemoryItem]]]
    )
    _seq: int = 0

    def store(
        self, content: str, *, wing: str, room: str, subject: str = "", now: float = 0.0
    ) -> str:
        self._seq += 1
        item = MemoryItem(
            id=f"d{self._seq}",
            kind="episodic",
            subject=subject or room,
            content=content,
            tags=[wing, room],
            created_at=now,
            valid_from=now,
        )
        self._index.setdefault(wing, {}).setdefault(room, []).append(item)
        return item.id

    def wings(self) -> list[str]:
        return sorted(self._index)

    def rooms(self, wing: str) -> list[str]:
        return sorted(self._index.get(wing, {}))

    def _in_scope(self, wing: str | None, room: str | None) -> list[MemoryItem]:
        out: list[MemoryItem] = []
        for w, rooms in self._index.items():
            if wing is not None and w != wing:
                continue
            for r, items in rooms.items():
                if room is not None and r != room:
                    continue
                out.extend(items)
        return out

    def recall(
        self,
        query: str,
        *,
        wing: str | None = None,
        room: str | None = None,
        k: int = 6,
        now: float | None = None,
    ) -> list[MemoryItem]:
        """Scoped recall: rank verbatim items WITHIN the wing/room scope (or palace-wide). Scoping
        is the precision win — a flat search over everything surfaces cross-topic noise."""
        scope = self._in_scope(wing, room)
        if not scope:
            return []
        clock = now if now is not None else float(self._seq + 1)
        return recall(scope, query, now=clock, k=k, max_chars=2000)


class PalaceBackend:
    """Our zero-API LocalPalace as a benchmark `MemoryBackend` — files each turn into a wing keyed
    by speaker; recall is palace-wide unless scoped. Pure, zero infra/LLM (the A/B comparison point
    against the flat FabricBackend)."""

    name = "madras-palace"

    def __init__(self, *, scope_by_speaker: bool = False) -> None:
        self._palace = LocalPalace()
        self._scope_by_speaker = scope_by_speaker

    async def ingest(self, turns: Sequence[Turn]) -> None:
        for i, t in enumerate(turns):
            self._palace.store(
                t.text, wing=t.speaker, room="conversation", subject=t.speaker, now=float(i)
            )

    async def recall(self, query: str, *, k: int) -> list[str]:
        hits = self._palace.recall(query, k=k)
        return [it.content for it in hits]


class _MemPalaceClient:
    """Adapts the mempalace library (add_drawer / search_memories) to the store/search interface
    `MemPalaceBackend` expects. Sync (mempalace is sync)."""

    def __init__(
        self, collection: Any, searcher: Any, miner: Any, palace_path: str, vector_disabled: bool
    ) -> None:
        self._col, self._se, self._mi = collection, searcher, miner
        self._pp, self._vd, self._n = palace_path, vector_disabled, 0

    def store(self, text: str, *, wing: str, room: str) -> None:
        self._mi.add_drawer(
            self._col,
            wing=wing,
            room=room,
            content=text,
            source_file="conversation",
            chunk_index=self._n,
            agent=wing,
        )
        self._n += 1

    def search(self, query: str, *, limit: int) -> list[str]:
        res: Any = self._se.search_memories(
            query, self._pp, n_results=limit, vector_disabled=self._vd
        )
        rows: list[Any] = (
            cast("dict[str, Any]", res).get("results", []) if isinstance(res, dict) else res
        )
        return [r.get("text", "") for r in rows]


class MemPalaceBackend:
    """Adapter over MemPalace (mempalace/mempalace, MIT) — the full zero-API local recall engine
    (verbatim drawers + structured wings/rooms + local search). Client injected (or a fake in
    tests); `connect()` builds a live `_MemPalaceClient`. Implements the `MemoryBackend` interface
    so it A/Bs against the Fabric in the [[Memory Benchmark]]."""

    name = "mempalace"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(
        cls,
        client_factory: Callable[[], Any] | None = None,
        *,
        palace_path: str = ".mempalace",
        vector_disabled: bool = True,
    ) -> MemPalaceBackend:
        """Wire live MemPalace (MIT) — a zero-API local recall engine. `get_collection(create=True)`
        bootstraps the palace; `vector_disabled` uses BM25 keyword recall (no embedding service)."""
        if client_factory is not None:
            return cls(client_factory())
        try:
            import mempalace.miner as miner
            import mempalace.searcher as searcher
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "MemPalace backend needs the `memory-palace` extra (mempalace, MIT) — a zero-API "
                "local recall engine (no embedding service required)"
            ) from exc
        # mempalace doesn't export get_collection via __all__, but it's the library's documented
        # bootstrap entry point (no public re-export to import instead).
        collection = searcher.get_collection(  # type: ignore[reportPrivateImportUsage]
            palace_path, create=True
        )
        return cls(_MemPalaceClient(collection, searcher, miner, palace_path, vector_disabled))

    async def ingest(self, turns: Sequence[Turn]) -> None:
        for t in turns:
            self._client.store(f"{t.speaker}: {t.text}", wing=t.speaker, room="conversation")

    async def recall(self, query: str, *, k: int) -> list[str]:
        return list(self._client.search(query, limit=k))
