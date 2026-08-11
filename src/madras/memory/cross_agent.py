"""Cross-agent session import — the amalgamation superpower.

Ingest OTHER agents' session transcripts (Claude Code / Codex / Hermes JSONL) into Madras's
Memory Fabric, so one memory spans every tool you use. Dedup by content hash (mirrors Codex's
external_agent_session_imports.json). Each transcript becomes a recallable episodic memory
(provenance-tagged); the raw turns are summarized to a compact item, not dumped wholesale.
Pure parsing + the fabric.remember contract — no LLM required.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from madras.memory.retrieval import MemoryItem

_TEXT_ROLES = {"user", "human", "assistant", "ai", "model"}


@dataclass
class ImportedSession:
    source_agent: str
    path: str
    content_sha256: str
    turns: int
    first_prompt: str


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _extract_turn(obj: dict[str, Any]) -> tuple[str, str]:
    """Pull (role, text) from one transcript line across common shapes (Claude Code / Codex)."""
    raw_msg = obj.get("message")
    msg = cast("dict[str, Any]", raw_msg) if isinstance(raw_msg, dict) else obj
    role = str(msg.get("role") or obj.get("type") or "").lower()
    content = msg.get("content")
    if isinstance(content, list):  # content-block arrays
        blocks = cast("list[Any]", content)
        dict_blocks = [cast("dict[str, Any]", b) for b in blocks if isinstance(b, dict)]
        content = " ".join(b.get("text", "") for b in dict_blocks if b.get("type") == "text")
    return role, str(content or "").strip()


def parse_jsonl_transcript(text: str) -> list[dict[str, str]]:
    """Extract role/text turns from a JSONL transcript. Skips non-message lines."""
    turns: list[dict[str, str]] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(obj, dict):
            continue
        role, content = _extract_turn(cast("dict[str, Any]", obj))
        if role in _TEXT_ROLES and content:
            turns.append({"role": role, "text": content})
    return turns


def _first_prompt(turns: list[dict[str, str]]) -> str:
    return next((t["text"] for t in turns if t["role"] in {"user", "human"}), "")


async def import_transcript(
    fabric: Any,
    path: str | Path,
    *,
    source_agent: str,
    tenant: str = "default",
    seen: set[str] | None = None,
    now: float,
) -> ImportedSession | None:
    """Import one transcript into the fabric as a recallable episodic memory. Dedup via `seen`
    (a set of content hashes). Returns None if empty or already imported."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    h = _sha(text)
    if seen is not None and h in seen:
        return None  # already imported (content-hash dedup)
    turns = parse_jsonl_transcript(text)
    if not turns:
        return None
    first = _first_prompt(turns)
    snippet = " | ".join(t["text"][:120] for t in turns[:3])
    item = MemoryItem(
        id=uuid.uuid4().hex,
        kind="imported_session",
        subject=f"{source_agent} session: {first[:60]}" if first else f"{source_agent} session",
        content=(
            f"Imported {source_agent} session ({len(turns)} turns). "
            f"First ask: {first[:300]}\n{snippet}"
        ),
        source=f"import:{source_agent}",
        agent_name="shadow",
        created_at=now,
        valid_from=now,
        tags=["imported", source_agent],
    )
    await fabric.remember(item, now=now)
    if seen is not None:
        seen.add(h)
    return ImportedSession(source_agent, str(p), h, len(turns), first)


async def import_dir(
    fabric: Any,
    root: str | Path,
    *,
    source_agent: str,
    seen: set[str] | None = None,
    now: float,
    pattern: str = "*.jsonl",
) -> list[ImportedSession]:
    """Import every transcript under `root` (recursive). Dedup across the run via `seen`."""
    out: list[ImportedSession] = []
    for p in sorted(Path(root).rglob(pattern)):
        r = await import_transcript(fabric, p, source_agent=source_agent, seen=seen, now=now)
        if r is not None:
            out.append(r)
    return out
