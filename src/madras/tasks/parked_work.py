"""Parked-work durability — zero-compute suspend + idempotent resume (row 86, eve pattern).

When a turn must wait for an external input (approval / OAuth callback / a child agent), it should
PARK: persist the turn state + a completion token, RELEASE all compute (no held coroutine/thread),
and resume from durable state when the input arrives — **exactly once**. Lifts eve's turn-workflow
park + `resumeHook`; the idempotent resume (a re-delivered resume or an interrupted-step re-run
executes at most once) ties to the row-84 idempotency doctrine. Pure core + injectable `ParkStore`
(in-memory default; durable Postgres over the scheduler table = a thin adapter).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ParkRecord:
    token: str
    session_id: str
    reason: str  # approval | oauth | child | clarify
    awaited: str  # a human description of what it's waiting for
    state: dict[str, Any]  # the serialized turn state to resume
    resumed: bool = False


@runtime_checkable
class ParkStore(Protocol):
    def save(self, record: ParkRecord) -> None: ...
    def load(self, token: str) -> ParkRecord | None: ...
    def set_resumed(self, token: str) -> bool: ...  # atomic CAS; False if already resumed/missing


@dataclass
class InMemoryParkStore:
    """Default store (durable Postgres over the scheduler table = a thin adapter)."""

    _records: dict[str, ParkRecord] = field(default_factory=dict[str, ParkRecord])

    def save(self, record: ParkRecord) -> None:
        self._records[record.token] = record

    def load(self, token: str) -> ParkRecord | None:
        return self._records.get(token)

    def set_resumed(self, token: str) -> bool:
        rec = self._records.get(token)
        if rec is None or rec.resumed:
            return False
        rec.resumed = True
        return True


@dataclass
class DurableWorldParkStore:
    """A `ParkStore` over a `DurableWorld` (row 87, the eve pattern's intended composition --
    ParkManager's own docstring: "durable Postgres over the scheduler table = a thin adapter").
    Namespaced under "parks"; `record.state` must be JSON-safe (the caller's responsibility --
    e.g. plain dicts/lists/strings, not live objects)."""

    world: Any  # DurableWorld
    ns: str = "parks"

    def save(self, record: ParkRecord) -> None:
        self.world.put(
            self.ns,
            record.token,
            {
                "token": record.token,
                "session_id": record.session_id,
                "reason": record.reason,
                "awaited": record.awaited,
                "state": record.state,
                "resumed": record.resumed,
            },
        )

    def load(self, token: str) -> ParkRecord | None:
        raw = self.world.get(self.ns, token)
        if raw is None:
            return None
        return ParkRecord(
            token=raw["token"],
            session_id=raw["session_id"],
            reason=raw["reason"],
            awaited=raw["awaited"],
            state=raw["state"],
            resumed=bool(raw.get("resumed", False)),
        )

    def set_resumed(self, token: str) -> bool:
        raw = self.world.get(self.ns, token)
        if raw is None or raw.get("resumed"):
            return False
        raw["resumed"] = True
        self.world.put(self.ns, token, raw)
        return True


@dataclass
class ResumeResult:
    ok: bool
    state: dict[str, Any] | None = None
    payload: Any = None
    reason: str = ""


@dataclass
class ParkManager:
    store: ParkStore
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, event: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit({"event": f"park_{event}", **kw})

    def park(
        self, *, token: str, session_id: str, reason: str, awaited: str, state: dict[str, Any]
    ) -> ParkRecord:
        """Persist the turn state, then return so the caller RELEASES all compute (zero-compute)."""
        record = ParkRecord(token, session_id, reason, awaited, dict(state))
        self.store.save(record)
        self._audit("parked", token=token, session=session_id, reason=reason, awaited=awaited)
        return record

    def resume(self, token: str, payload: Any = None) -> ResumeResult:
        """Resume a parked turn from durable state — exactly once (idempotent)."""
        record = self.store.load(token)
        if record is None:
            self._audit("resume_unknown", token=token)
            return ResumeResult(False, reason="unknown park token")
        if not self.store.set_resumed(token):
            self._audit("resume_duplicate", token=token)
            return ResumeResult(False, reason="already resumed (interrupted-step idempotency)")
        self._audit("resumed", token=token, session=record.session_id, reason=record.reason)
        return ResumeResult(True, state=record.state, payload=payload, reason="resumed")

    def is_parked(self, token: str) -> bool:
        record = self.store.load(token)
        return record is not None and not record.resumed
