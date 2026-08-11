"""Tamper-evident hash chain for the audit log.

The audit log is already append-only (no update/delete). The chain makes it
tamper-EVIDENT: each record commits to the previous record's hash, so altering or
deleting any historical row breaks every hash downstream. A verifier can recompute
the chain from the stored payloads and detect the first broken link.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

GENESIS = "0" * 64  # prev_hash of the first record in a session


def canonical_payload(
    *,
    agent_name: str,
    session_id: str,
    action: str,
    signals: dict[str, Any],
    tool_calls: list[Any],
    extras: dict[str, Any],
) -> str:
    """Deterministic JSON of the record's content (sorted keys) — the bytes hashed."""
    return json.dumps(
        {
            "agent_name": agent_name,
            "session_id": session_id,
            "action": action,
            "signals": signals,
            "tool_calls": tool_calls,
            "extras": extras,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_hash(prev_hash: str, payload: str) -> str:
    """record_hash = sha256(prev_hash || payload)."""
    h = hashlib.sha256()
    h.update((prev_hash or GENESIS).encode("utf-8"))
    h.update(payload.encode("utf-8"))
    return h.hexdigest()


@dataclass
class ChainResult:
    ok: bool
    length: int
    broken_at: int | None = None  # index of the first tampered record, if any


def verify_chain(records: list[dict[str, Any]]) -> ChainResult:
    """Recompute the chain over ordered records.

    Each record must carry: prev_hash, record_hash, and the canonical fields
    (agent_name, session_id, action, signals, tool_calls, extras). Returns the
    index of the first broken link, or ok=True if every link holds.
    """
    prev = GENESIS
    for i, rec in enumerate(records):
        if rec.get("prev_hash", GENESIS) != prev:
            return ChainResult(ok=False, length=len(records), broken_at=i)
        payload = canonical_payload(
            agent_name=rec["agent_name"],
            session_id=rec["session_id"],
            action=rec["action"],
            signals=rec.get("signals", {}),
            tool_calls=rec.get("tool_calls", []),
            extras=rec.get("extras", {}),
        )
        expected = compute_hash(prev, payload)
        if expected != rec.get("record_hash"):
            return ChainResult(ok=False, length=len(records), broken_at=i)
        prev = expected
    return ChainResult(ok=True, length=len(records))
