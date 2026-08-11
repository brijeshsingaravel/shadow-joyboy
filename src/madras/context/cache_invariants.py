"""Prompt-cache invariants — keep the cacheable prefix stable + append-only (row 70).

Cache reads cost ~10% of normal input (a ~90% saving), so a STABLE prefix is the zero-cost moat.
The rules (Hermes/OpenClaw + 2026 best practice): deterministic tool/prefix ordering; never mutate
past context or swap the toolset mid-conversation (use tool-search to APPEND to messages, not swap
the tools array); no silent invalidators (timestamps / UUIDs / per-request ids) in the prefix. This
is a pure, always-on guard the context builder runs to catch cache-busting BEFORE it costs money:
`canonical_tools` fixes ordering, `stable_prefix_key` is the should-be cache key, `check_invariants`
catches what would actually bust the provider cache, and `enforce(strict=True)` can reject it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any


class CacheBustError(Exception):
    """Raised by enforce(strict=True) when a context change would invalidate the prompt cache."""


@dataclass
class CacheViolation:
    kind: str  # volatile_prefix | tool_swap | tool_reorder | past_mutated
    detail: str


def _tool_name(t: dict[str, Any]) -> str:
    return str(t.get("name") or t.get("function", {}).get("name", ""))


def canonical_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic stable ordering — sort by tool name so the prefix bytes are identical every
    request (dict/set iteration order silently invalidates the cache)."""
    return sorted(tools, key=_tool_name)


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def stable_prefix_key(*, system: str, tools: list[dict[str, Any]]) -> str:
    """Canonical hash of the cacheable prefix (system + order-normalized tools). Same inputs →
    same key → a cache hit. Order-independent by construction (uses canonical_tools)."""
    blob = system + "\x00" + _canon([_canon(t) for t in canonical_tools(tools)])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


_VOLATILE: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}"), "timestamp"),
    (
        re.compile(
            r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
            r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
        ),
        "uuid",
    ),
    (re.compile(r"(?:session|request|trace)[_-]?id\s*[:=]\s*\S+", re.I), "session_id"),
    (re.compile(r"\b\d{10,13}\b"), "epoch"),
]


def scan_volatile(text: str) -> list[tuple[str, str]]:
    """Flag silent cache-invalidators in the (supposedly static) prefix: (kind, matched-snippet)."""
    out: list[tuple[str, str]] = []
    for rx, name in _VOLATILE:
        m = rx.search(text or "")
        if m:
            out.append((name, m.group(0)[:40]))
    return out


@dataclass
class PrefixSnapshot:
    """A point-in-time view of the cacheable prefix + the append-only history order."""

    system: str = ""
    tools: list[dict[str, Any]] = field(
        default_factory=list[dict[str, Any]]
    )  # in PRESENTATION order
    history_keys: list[str] = field(default_factory=list[str])  # stable per-turn keys, in order


def check_invariants(prev: PrefixSnapshot, new: PrefixSnapshot) -> list[CacheViolation]:
    """Compare two snapshots; return every cache-busting change (empty = cache-stable)."""
    v: list[CacheViolation] = []

    for kind, hit in scan_volatile(new.system):
        v.append(CacheViolation("volatile_prefix", f"{kind} in system prompt: {hit!r}"))

    prev_names = [_tool_name(t) for t in prev.tools]
    new_names = [_tool_name(t) for t in new.tools]
    removed = set(prev_names) - set(new_names)
    added = set(new_names) - set(prev_names)
    if removed:
        v.append(CacheViolation("tool_swap", f"tools removed mid-convo: {sorted(removed)}"))
    if added:
        v.append(
            CacheViolation(
                "tool_swap",
                f"tools added to tools[] mid-convo (append to messages via tool-search instead): "
                f"{sorted(added)}",
            )
        )
    if not removed and not added and prev_names != new_names:
        v.append(CacheViolation("tool_reorder", "tool order changed — serialize deterministically"))

    n = len(prev.history_keys)
    if new.history_keys[:n] != prev.history_keys:
        v.append(
            CacheViolation(
                "past_mutated", "past messages reordered/edited — history must be append-only"
            )
        )
    return v


def is_cache_stable(prev: PrefixSnapshot, new: PrefixSnapshot) -> bool:
    return not check_invariants(prev, new)


def enforce(
    prev: PrefixSnapshot, new: PrefixSnapshot, *, strict: bool = False, audit: Any = None
) -> list[CacheViolation]:
    """Always-on guard: returns violations (the signal). With strict=True, RAISES CacheBustError
    on any violation (reject the cache-busting context); else flags + emits a stability signal."""
    violations = check_invariants(prev, new)
    if violations and audit is not None:
        audit({"event": "cache_bust", "violations": [(x.kind, x.detail) for x in violations]})
    if violations and strict:
        raise CacheBustError("; ".join(f"{x.kind}: {x.detail}" for x in violations))
    return violations
