"""Principle reflection (Step 4, L5) — distil durable principles from memories.

Generative-Agents reflection: periodically synthesise higher-level, reusable PRINCIPLES
from recurring lower-level memories (facts/preferences). Principles are stored back into
the fabric as kind="principle" — they ACCRUE (never superseded) and are loaded once at
session start (≤ a token budget). This is the "self-evolving library": hand-written
skills + principles the agent learned on its own.

Pure + deterministic here (clustering + a fallback principle draft); the Memory Manager
may pass an LLM for richer phrasing. Fully testable without DB/LLM.
"""

from __future__ import annotations

from collections import defaultdict

from madras.memory.retrieval import MemoryItem, is_current

_REFLECTABLE = {"fact", "preference"}


def reflection_clusters(
    items: list[MemoryItem], now: float, *, min_support: int = 2
) -> list[tuple[str, list[MemoryItem]]]:
    """Group currently-valid fact/preference items by subject; return the clusters with
    at least ``min_support`` members (a subject the user kept reinforcing/elaborating —
    worth distilling into a principle). Sorted by support, descending."""
    by_subject: dict[str, list[MemoryItem]] = defaultdict(list)
    for it in items:
        if it.kind in _REFLECTABLE and is_current(it, now) and it.subject.strip():
            by_subject[it.subject.strip().lower()].append(it)
    clusters = [(s, v) for s, v in by_subject.items() if len(v) >= min_support]
    clusters.sort(key=lambda c: len(c[1]), reverse=True)
    return clusters


def draft_principle(subject: str, members: list[MemoryItem]) -> str:
    """Deterministic fallback principle text from a cluster (the LLM can do better)."""
    seen: list[str] = []
    for m in members:
        c = m.content.strip()
        if c and c not in seen:
            seen.append(c)
    body = "; ".join(seen)
    return f"On {subject}: {body}"[:240]


def principle_exists(existing: list[MemoryItem], subject: str) -> bool:
    """True if a principle for this subject is already on record (dedup — accrue, not spam)."""
    s = subject.strip().lower()
    return any(p.kind == "principle" and p.subject.strip().lower() == s for p in existing)


def load_principles(
    items: list[MemoryItem], now: float, *, max_chars: int = 5000
) -> list[MemoryItem]:
    """The Principle Layer loaded once at session start: current principles, newest first,
    trimmed to a token/char budget (stays resident; ≤ budget)."""
    principles = [i for i in items if i.kind == "principle" and is_current(i, now)]
    principles.sort(key=lambda i: i.created_at, reverse=True)
    out: list[MemoryItem] = []
    used = 0
    for p in principles:
        used += len(p.content)
        if out and used > max_chars:
            break
        out.append(p)
    return out
