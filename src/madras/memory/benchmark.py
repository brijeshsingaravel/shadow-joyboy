"""Memory-backend A/B benchmark — finally MEASURE the Fabric (LoCoMo / LongMemEval).

Closes the standing gap ("we've never measured ours"). An injectable `MemoryBackend` lets us run
the SAME long-multi-session-QA dataset through different memory systems and compare on two axes:
**recall accuracy** (does the backend surface the answer-bearing evidence?) and **context cost**
(how many chars it puts in front of the model — Mem0's efficiency win is high accuracy at LOW
tokens). `FabricBackend` runs our pure retrieval core (zero infra, zero LLM); `FullContextBackend`
is the dump-everything baseline; `Mem0Backend` is the injectable external comparison (Apache-2.0).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast, runtime_checkable

from madras.memory.retrieval import MemoryItem, recall


@dataclass
class Turn:
    speaker: str
    text: str


@dataclass
class QA:
    question: str
    answer: str


@runtime_checkable
class MemoryBackend(Protocol):
    name: str

    async def ingest(self, turns: Sequence[Turn]) -> None: ...
    async def recall(self, query: str, *, k: int) -> list[str]: ...  # answer-bearing snippets


@dataclass
class BenchResult:
    backend: str
    n: int
    hits: int
    avg_context_chars: float
    full_context_chars: int

    @property
    def accuracy(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def context_reduction(self) -> float:
        """Fraction of the full conversation NOT put in context (the efficiency win)."""
        if not self.full_context_chars:
            return 0.0
        return 1.0 - (self.avg_context_chars / self.full_context_chars)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").lower()).strip()


async def run_memory_benchmark(
    backend: MemoryBackend,
    turns: Sequence[Turn],
    qas: Sequence[QA],
    *,
    k: int = 6,
) -> BenchResult:
    """Ingest the conversation once, then recall per question; a hit = the gold answer appears in
    the recalled snippets (the backend surfaced the right evidence)."""
    await backend.ingest(turns)
    full_chars = sum(len(t.text) for t in turns)
    hits = 0
    ctx_total = 0
    for qa in qas:
        snippets = await backend.recall(qa.question, k=k)
        ctx = " ".join(snippets)
        ctx_total += len(ctx)
        if _norm(qa.answer) and _norm(qa.answer) in _norm(ctx):
            hits += 1
    n = len(qas)
    return BenchResult(backend.name, n, hits, ctx_total / n if n else 0.0, full_chars)


class FabricBackend:
    """Our Memory Fabric retrieval core (pure, zero infra/LLM): each conversation turn becomes
    an episodic MemoryItem; `recall` is the Fabric's relevance-x-recency ranking, top-k turns."""

    name = "madras-fabric"

    def __init__(self) -> None:
        self._items: list[MemoryItem] = []

    async def ingest(self, turns: Sequence[Turn]) -> None:
        self._items = [
            MemoryItem(
                id=f"t{i}",
                kind="episodic",
                subject=t.speaker,
                content=t.text,
                created_at=float(i),
                valid_from=float(i),
            )
            for i, t in enumerate(turns)
        ]

    async def recall(self, query: str, *, k: int) -> list[str]:
        now = float(len(self._items) + 1)
        hits = recall(self._items, query, now=now, k=k, max_chars=2000)
        return [it.content for it in hits]


class FullContextBackend:
    """Dump-everything baseline (= the old LoCoMo suite): perfect recall, maximal context cost."""

    name = "full-context-baseline"

    def __init__(self) -> None:
        self._all: list[str] = []

    async def ingest(self, turns: Sequence[Turn]) -> None:
        self._all = [t.text for t in turns]

    async def recall(self, query: str, *, k: int) -> list[str]:
        return list(self._all)


class Mem0Backend:
    """Adapter over Mem0 (mem0ai/mem0, Apache-2.0) — the external A/B comparison. The client is
    injected (or a fake in tests); `connect()` lazy-imports `mem0`. Live: Mem0 does LLM fact
    extraction + semantic recall; route its LLM via LiteLLM (zero-cost local) for the A/B run."""

    name = "mem0"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(
        cls,
        client_factory: Callable[[], Any] | None = None,
        *,
        llm_model: str = "llama3.2:3b",
        embed_model: str = "nomic-embed-text",
        ollama_host: str = "http://127.0.0.1:11434",
        chroma_path: str = ".mem0-chroma",
    ) -> Mem0Backend:
        """Wire live Mem0 (Apache-2.0): LLM fact-extraction + semantic recall, all on a local
        **Ollama** GPU model + nomic-embed-text + a local Chroma store (zero-cost, uses VRAM)."""
        if client_factory is not None:
            return cls(client_factory())
        try:
            from mem0 import AsyncMemory  # type: ignore[reportMissingTypeStubs]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Mem0 backend needs the `memory-bench` extra (mem0ai, Apache-2.0) + a local Ollama "
                "model for a zero-cost A/B run against the Fabric"
            ) from exc
        config = {
            "llm": {
                "provider": "ollama",
                "config": {"model": llm_model, "ollama_base_url": ollama_host},
            },
            "embedder": {
                "provider": "ollama",
                "config": {"model": embed_model, "ollama_base_url": ollama_host},
            },
            "vector_store": {
                "provider": "chroma",
                "config": {"collection_name": "madras_mem0", "path": chroma_path},
            },
        }
        return cls(AsyncMemory.from_config(config))

    async def ingest(self, turns: Sequence[Turn]) -> None:
        msgs = [{"role": "user", "content": f"{t.speaker}: {t.text}"} for t in turns]
        await self._client.add(msgs, user_id="bench")

    async def recall(self, query: str, *, k: int) -> list[str]:
        res: Any = await self._client.search(query, filters={"user_id": "bench"}, limit=k)
        rows: list[Any] = (
            cast("dict[str, Any]", res).get("results", res) if isinstance(res, dict) else res
        )
        return [m.get("memory", "") for m in rows]


# -- LoCoMo slice loader -------------------------------------------------------
_TURN_RE = re.compile(r"^([A-Z][\w'-]*?):\s+(.*)$")


def parse_conversation(conv: str) -> list[Turn]:
    """Parse a LoCoMo conversation string ('Speaker: text' lines, session headers) into Turns."""
    turns: list[Turn] = []
    for line in conv.splitlines():
        line = line.strip()
        if not line or line.startswith("---"):
            continue
        m = _TURN_RE.match(line)
        if m:
            turns.append(Turn(speaker=m.group(1), text=m.group(2).strip()))
    return turns
