"""Pluggable durability "world" adapter — swap the durable backend (row 87, eve pattern).

LangGraph's `PostgresSaver` is fixed; eve swaps `@workflow/world-postgres` and runs a local on-disk
world in dev. This is that abstraction: a namespaced durable KV behind ONE `DurableWorld` Protocol,
so the checkpointer / scheduler / parked-work can resolve through one swappable backend —
**in-memory for tests, on-disk JSON for dev (no Postgres needed), Postgres for prod** — same
interface everywhere, no drift. Namespaced (`checkpoints`/`schedules`/`parks`), tenant-isolatable.
Pure stdlib.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

_MISSING = object()


@runtime_checkable
class DurableWorld(Protocol):
    def put(self, ns: str, key: str, value: Any) -> None: ...
    def get(self, ns: str, key: str) -> Any | None: ...
    def delete(self, ns: str, key: str) -> bool: ...
    def keys(self, ns: str) -> list[str]: ...


def _copy(value: Any) -> Any:
    return json.loads(json.dumps(value))  # durable semantics: store a snapshot, not a live ref


@dataclass
class MemoryWorld:
    """In-memory durable world (tests / ephemeral)."""

    _data: dict[str, dict[str, Any]] = field(default_factory=dict[str, dict[str, Any]])

    def put(self, ns: str, key: str, value: Any) -> None:
        self._data.setdefault(ns, {})[key] = _copy(value)

    def get(self, ns: str, key: str) -> Any | None:
        return self._data.get(ns, {}).get(key)

    def delete(self, ns: str, key: str) -> bool:
        return self._data.get(ns, {}).pop(key, _MISSING) is not _MISSING

    def keys(self, ns: str) -> list[str]:
        return sorted(self._data.get(ns, {}))


def _safe_ns(ns: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in ns) or "_"


@dataclass
class FileWorld:
    """On-disk JSON durable world (local dev) — durable across process restarts, no Postgres. One
    file per namespace under `root` (atomic write via a temp file + os.replace)."""

    root: str

    def _path(self, ns: str) -> str:
        return os.path.join(self.root, f"{_safe_ns(ns)}.json")

    def _load(self, ns: str) -> dict[str, Any]:
        path = self._path(ns)
        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                return json.loads(fh.read() or "{}")
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self, ns: str, data: dict[str, Any]) -> None:
        os.makedirs(self.root, exist_ok=True)
        tmp = self._path(ns) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, ensure_ascii=False))
        os.replace(tmp, self._path(ns))

    def put(self, ns: str, key: str, value: Any) -> None:
        data = self._load(ns)
        data[key] = value
        self._save(ns, data)

    def get(self, ns: str, key: str) -> Any | None:
        return self._load(ns).get(key)

    def delete(self, ns: str, key: str) -> bool:
        data = self._load(ns)
        if key not in data:
            return False
        del data[key]
        self._save(ns, data)
        return True

    def keys(self, ns: str) -> list[str]:
        return sorted(self._load(ns))


class PostgresWorld:
    """Prod durable world — a thin adapter over an injected Postgres-backed KV client (`connect()`
    lazy-imports asyncpg). The live wiring points at a `madras_world` table; same Protocol."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(cls, client_factory: Callable[[], Any] | None = None) -> PostgresWorld:
        if client_factory is not None:
            return cls(client_factory())
        try:
            import asyncpg  # noqa: F401  # pyright: ignore[reportUnusedImport]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "the prod durable world needs `asyncpg` + a `madras_world` table; provide a "
                "configured client via client_factory"
            ) from exc
        raise RuntimeError("provide a configured Postgres world client via client_factory")

    def put(self, ns: str, key: str, value: Any) -> None:
        self._client.put(ns, key, value)

    def get(self, ns: str, key: str) -> Any | None:
        return self._client.get(ns, key)

    def delete(self, ns: str, key: str) -> bool:
        return self._client.delete(ns, key)

    def keys(self, ns: str) -> list[str]:
        return self._client.keys(ns)


def world_for(
    env: str, *, root: str = ".madras/world", pg_client_factory: Callable[[], Any] | None = None
) -> DurableWorld:
    """Select the durable world by environment: test → in-memory, dev → on-disk, prod → Postgres."""
    e = (env or "").lower()
    if e in ("dev", "local"):
        return FileWorld(root)
    if e in ("prod", "production"):
        return PostgresWorld.connect(pg_client_factory)
    return MemoryWorld()  # test / default — safe + ephemeral
