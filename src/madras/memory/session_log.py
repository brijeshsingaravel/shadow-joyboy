"""Session-log RAG — index the raw build-session transcripts as L2 episodic memory + recall them.

The raw Claude Code transcripts (``Knowledge/Sessions/raw/*.jsonl``) are the founder+Claude build
sessions ([[Sessions]]). This chunks them into *moments* (a user prompt + the work it drove), embeds
each via the Memory Fabric's Ollama embedder, and indexes them in a Qdrant collection
(``madras_session_logs``) for semantic recall at session start. Reuses ``memory.vector`` — not a
parallel RAG. Fully degrading: if Qdrant/Ollama are down, index/recall return silently.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import httpx

from madras.config import settings
from madras.memory.vector import embed, qdrant_headers

_FILE_TOOLS_INPUT_KEY = "file_path"  # Edit/Write/Read/NotebookEdit all carry file_path

# A *pure* acknowledgment prompt — "yes", "okay proceed", "go ahead" with nothing else. These open a
# moment but carry no intent of their own; when they also drove no real work (no files, only a short
# reply) the moment is pure noise that matches queries generically, so the chunker drops it (W3).
_ACK_WORD = (
    r"yes|yep|yeah|ok|okay|k|sure|continue|proceed|ahead|on|do it|great|thanks|thank you|"
    r"perfect|good|nice|cool|next|done|agreed|approved|correct|right|go|let's"
)
# one or more ack words back-to-back (+ filler punctuation): "yes", "okay proceed", "go ahead".
_TRIVIAL_ACK = re.compile(rf"^(?:(?:{_ACK_WORD})\b[\s\W]*)+$", re.IGNORECASE)
_MIN_WORK_CHARS = 400  # below this (and no files) an ack-led moment carries no recallable signal


@dataclass
class Moment:
    """One coherent unit of a build session: a user prompt + the assistant work it drove."""

    session_id: str
    seq: int
    text: str
    files_touched: list[str] = field(default_factory=list[str])
    ts: str = ""
    prompt: str = ""  # the founder's intent that opened the moment — anchors the embedding (W3)

    @property
    def point_key(self) -> str:
        return f"{self.session_id}:{self.seq}"

    @property
    def embed_text(self) -> str:
        """Intent-led text fed to the embedder: lead with the founder's ask so recall anchors on
        *what was asked*, not the incidental assistant prose that dominates by length (W3)."""
        return f"USER INTENT: {self.prompt}\n\n{self.text}" if self.prompt else self.text


def _is_noise(prompt: str, work: str, files: list[str]) -> bool:
    """A moment is noise iff its prompt is a pure acknowledgment AND it drove no real work
    (no files touched, only a short reply). Conservative: anything substantive is kept."""
    return bool(_TRIVIAL_ACK.match(prompt.strip())) and not files and len(work) < _MIN_WORK_CHARS


def _content(event: dict[str, Any]) -> str | list[Any]:
    message: dict[str, Any] = event.get("message") or {}
    content: Any = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return cast("list[Any]", content)
    return ""


def _is_user_prompt(event: dict[str, Any]) -> bool:
    """A real founder prompt — not a tool_result (which is also a 'user'-typed event) or meta."""
    if event.get("type") != "user" or event.get("isMeta"):
        return False
    c = _content(event)
    if isinstance(c, str):
        return bool(c.strip())
    blocks: list[dict[str, Any]] = [b for b in c if isinstance(b, dict)]
    return any(b.get("type") == "text" and (b.get("text") or "").strip() for b in blocks)


def _text_of(event: dict[str, Any]) -> str:
    c = _content(event)
    if isinstance(c, str):
        return c
    blocks: list[dict[str, Any]] = [b for b in c if isinstance(b, dict)]
    return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text" and b.get("text"))


def _files_of(event: dict[str, Any]) -> list[str]:
    c = _content(event)
    out: list[str] = []
    if isinstance(c, list):
        for b in c:
            if isinstance(b, dict):
                b = cast("dict[str, Any]", b)
                if b.get("type") == "tool_use":
                    tool_input: dict[str, Any] = b.get("input") or {}
                    fp = tool_input.get(_FILE_TOOLS_INPUT_KEY)
                    if fp:
                        out.append(str(fp))
    return out


def chunk_transcript(path: Path, *, max_chars: int = 4000) -> list[Moment]:
    """Group a raw ``.jsonl`` transcript into moments — one per real user prompt, carrying the
    assistant text it drove + the files it touched. Never crashes on a malformed line."""
    path = Path(path)
    moments: list[Moment] = []
    prompt = ""
    work: list[str] = []
    files: list[str] = []
    ts = ""
    sid = ""
    seq = 0

    def flush() -> None:
        nonlocal seq, prompt, work, files
        if prompt or work:
            work_text = "\n".join(work)
            if not _is_noise(prompt, work_text, files):
                text = f"{prompt}\n{work_text}".strip()[:max_chars]
                moments.append(Moment(sid or path.stem, seq, text, sorted(set(files)), ts, prompt))
                seq += 1
            prompt, work, files = "", [], []

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        event = cast("dict[str, Any]", event)
        if event.get("sessionId"):
            sid = event["sessionId"]
        if _is_user_prompt(event):
            flush()
            ts = event.get("timestamp", ts)
            prompt = _text_of(event)
        elif event.get("type") == "assistant":
            t = _text_of(event)
            if t:
                work.append(t)
            files.extend(_files_of(event))
    flush()
    return moments


_COLLECTION = "madras_session_logs"


def _point_id(key: str) -> str:
    # Qdrant point ids must be uint or UUID — derive a stable UUID5 from "session:seq".
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


async def _embed_many(texts: list[str], *, concurrency: int = 4) -> list[list[float] | None]:
    """Embed many texts with bounded concurrency. Concurrency is deliberately low (4): a CPU-bound
    Ollama serialises anyway, and over-subscribing pushes full-size (~4k char) embeds past the 30s
    per-call timeout — which silently drops moments. A timed-out embed is retried once alone."""
    sem = asyncio.Semaphore(concurrency)

    async def _one(t: str) -> list[float] | None:
        async with sem:
            vec = await embed(t)
            return vec if vec is not None else await embed(t)  # retry a single failure alone

    return list(await asyncio.gather(*[_one(t) for t in texts]))


class SessionLogIndex:
    """Index session-log moments into Qdrant + recall them. Reuses ``memory.vector.embed`` (Ollama
    nomic-embed); fully degrading — if Qdrant/Ollama are down, index/recall return silently."""

    def __init__(self, *, collection: str = _COLLECTION) -> None:
        self._col = collection

    async def _ensure(self, client: httpx.AsyncClient, size: int) -> None:
        url = f"{settings.qdrant_url}/collections/{self._col}"
        try:
            r = await client.get(url)
            if r.status_code != 200:
                await client.put(url, json={"vectors": {"size": size, "distance": "Cosine"}})
        except httpx.HTTPError:
            await client.put(url, json={"vectors": {"size": size, "distance": "Cosine"}})

    async def index_session(self, path: Path) -> int:
        """Chunk + embed + upsert a transcript's moments (idempotent by id). Returns the count."""
        moments = chunk_transcript(path)
        if not moments:
            return 0
        src = Path(path).name
        vecs = await _embed_many([m.embed_text for m in moments])
        points: list[dict[str, Any]] = []
        for m, vec in zip(moments, vecs, strict=True):
            if vec is None:
                continue
            points.append(
                {
                    "id": _point_id(m.point_key),
                    "vector": vec,
                    "payload": {
                        "session_id": m.session_id,
                        "seq": m.seq,
                        "text": m.text,
                        "files_touched": m.files_touched,
                        "ts": m.ts,
                        "source": src,
                    },
                }
            )
        if not points:
            return 0
        async with httpx.AsyncClient(timeout=60.0, headers=qdrant_headers()) as client:
            await self._ensure(client, len(points[0]["vector"]))
            for i in range(0, len(points), 64):  # batch — a big transcript's single upsert can fail
                await client.put(
                    f"{settings.qdrant_url}/collections/{self._col}/points",
                    json={"points": points[i : i + 64]},
                )
        return len(points)

    async def recall(self, query: str, k: int = 5) -> list[dict[str, Any]]:
        """Semantic recall — return the top-k cited moment payloads for ``query``."""
        vec = await embed(query)
        if vec is None:
            return []
        try:
            async with httpx.AsyncClient(timeout=20.0, headers=qdrant_headers()) as client:
                r = await client.post(
                    f"{settings.qdrant_url}/collections/{self._col}/points/search",
                    json={"vector": vec, "limit": k, "with_payload": True},
                )
                if r.status_code == 404:
                    return []
                r.raise_for_status()
                return [h["payload"] for h in r.json().get("result", []) if h.get("payload")]
        except Exception:
            return []

    async def drop(self) -> None:
        """Delete the collection (keeps tests idempotent)."""
        try:
            async with httpx.AsyncClient(timeout=15.0, headers=qdrant_headers()) as client:
                await client.delete(f"{settings.qdrant_url}/collections/{self._col}")
        except Exception:
            pass


async def recall_session_context(query: str, k: int = 5) -> list[dict[str, Any]]:
    """Semantic recall over the raw build-session logs (the grounding-pack Tier-1 hook)."""
    return await SessionLogIndex().recall(query, k=k)
