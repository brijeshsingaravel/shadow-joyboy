"""Governed connector library — the 600+ tools of ACI.dev, but every call governed.

Backend finalized: **ACI.dev** (aipotheosis-labs/aci, Apache-2.0) — 600+ tools via a unified MCP
server + direct function calling, with built-in OAuth + secrets + multi-tenant auth. The Madras
edge wraps it: connectors are **discovered by relevance** (surface the few, not all 600 — the
RAG-find / [[Deferred Capability Loading]] principle), and every call is **approval-gated when
mutating**, **JIT-credentialed** (OAuth/api-key resolved per task, ASI03), and **audited**. Pure +
deterministic; the ACI gateway is an injectable adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class Connector:
    name: str  # unique id, e.g. "github__create_issue"
    app: str  # "github"
    description: str = ""
    auth_type: str = "none"  # none | api_key | oauth
    scopes: tuple[str, ...] = ()
    mutating: bool = False


@dataclass
class ConnectorResult:
    ok: bool
    output: Any = None
    error: str | None = None


@runtime_checkable
class ConnectorBackend(Protocol):
    async def execute(
        self, connector: Connector, action: str, args: dict[str, Any], cred: str | None
    ) -> Any: ...


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._by_name[connector.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._by_name.get(name)

    def search(self, query: str, *, limit: int = 5) -> list[Connector]:
        """Relevance-search the catalog (token overlap on name/app/description) — surface the
        few relevant connectors, never the whole 600 into context."""
        terms = {t for t in query.lower().split() if t}
        scored: list[tuple[int, str, Connector]] = []
        for c in self._by_name.values():
            blob = f"{c.name} {c.app} {c.description}".lower()
            score = sum(1 for t in terms if t in blob)
            if score:
                scored.append((score, c.name, c))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [c for _, _, c in scored[:limit]]

    def __len__(self) -> int:
        return len(self._by_name)


@dataclass
class GovernedConnector:
    backend: ConnectorBackend
    registry: ConnectorRegistry
    approve: Callable[[Connector, str, dict[str, Any]], bool] | None = None
    cred_resolver: Callable[[Connector], str | None] | None = None
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def call(
        self, name: str, action: str, args: dict[str, Any] | None = None
    ) -> ConnectorResult:
        args = args or {}
        connector = self.registry.get(name)
        if connector is None:
            return ConnectorResult(False, error=f"unknown connector '{name}'")

        cred: str | None = None
        if connector.auth_type in ("oauth", "api_key"):
            cred = self.cred_resolver(connector) if self.cred_resolver is not None else None
            if not cred:
                self._audit({"event": "no_cred", "connector": name, "auth": connector.auth_type})
                return ConnectorResult(
                    False,
                    error=f"{connector.auth_type} credential required for "
                    f"'{connector.app}' (JIT, not configured)",
                )

        if (
            connector.mutating
            and self.approve is not None
            and not self.approve(connector, action, args)
        ):
            self._audit({"event": "denied", "connector": name, "action": action})
            return ConnectorResult(
                False, error=f"mutating action '{action}' on '{connector.app}' denied (approval)"
            )

        self._audit(
            {"event": "call", "connector": name, "action": action, "mutating": connector.mutating}
        )
        return ConnectorResult(True, await self.backend.execute(connector, action, args, cred))


class ACIBackend:
    """Adapter over ACI.dev (Apache-2.0). The ACI client is injected (or a fake in tests);
    `connect()` lazy-imports the optional `aci` SDK. Live wiring points at a self-hosted ACI
    instance and resolves OAuth via its secrets manager + Madras's JIT creds."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(cls, client_factory: Callable[[], Any] | None = None) -> ACIBackend:
        if client_factory is not None:
            return cls(client_factory())
        try:
            import aci  # noqa: F401  # type: ignore[reportMissingImports, reportUnusedImport]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "the ACI SDK is not installed — `pip install aci-sdk` (Apache-2.0) + a self-hosted "
                "ACI.dev instance to wire the live 600+ connector backend"
            ) from exc
        raise RuntimeError("provide a configured ACI client via client_factory")

    async def execute(
        self, connector: Connector, action: str, args: dict[str, Any], cred: str | None
    ) -> Any:
        return await self._client.call_function(connector.name, args, linked_account=cred)
