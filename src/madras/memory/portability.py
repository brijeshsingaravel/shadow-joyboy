"""Portable agent-memory export (E-X4b) — content-addressed, verifiable bundle.

Exports memories as a portable bundle: each item is canonicalized + content-hashed
(blake2b, stdlib — no blake3 dep), with a Merkle-style root over the sorted item hashes.
This is the interoperability leg alongside MCP (tools) + A2A (coordination), per [[memory]]
§7. An optional Ed25519 signer (injected) signs the root for provenance; absent it, the
bundle is still verifiable by recomputing the root. (Import is the existing E3 experience.)
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from typing import Any

from madras.memory.retrieval import MemoryItem

BUNDLE_VERSION = "madras-mem/1"


def _item_payload(it: MemoryItem) -> dict[str, Any]:
    return {
        "id": it.id,
        "kind": it.kind,
        "subject": it.subject,
        "content": it.content,
        "tags": sorted(it.tags),
        "confidence": it.confidence,
        "source": it.source,
        "created_at": it.created_at,
        "valid_from": it.valid_from,
        "valid_until": it.valid_until,
    }


def _hash(obj: Any) -> str:
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.blake2b(raw, digest_size=32).hexdigest()


def _root(hashes: list[str]) -> str:
    # order-independent Merkle-style root: hash the sorted leaf hashes
    return hashlib.blake2b("".join(sorted(hashes)).encode(), digest_size=32).hexdigest()


def export_memory(
    items: Iterable[MemoryItem],
    *,
    agent: str = "shadow",
    tenant: str = "default",
    sign: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Build a portable, content-addressed memory bundle. ``sign`` (optional) signs the root."""
    entries: list[dict[str, Any]] = []
    for it in items:
        payload = _item_payload(it)
        entries.append({"hash": _hash(payload), "item": payload})
    root = _root([e["hash"] for e in entries])
    bundle: dict[str, Any] = {
        "version": BUNDLE_VERSION,
        "agent": agent,
        "tenant": tenant,
        "count": len(entries),
        "root": root,
        "items": entries,
    }
    if sign is not None:
        bundle["signature"] = sign(root)
    return bundle


def verify_bundle(bundle: dict[str, Any]) -> bool:
    """True if every item hash + the root recompute correctly (tamper-evident)."""
    entries = bundle.get("items", [])
    for e in entries:
        if _hash(e.get("item")) != e.get("hash"):
            return False
    return _root([e.get("hash", "") for e in entries]) == bundle.get("root")
