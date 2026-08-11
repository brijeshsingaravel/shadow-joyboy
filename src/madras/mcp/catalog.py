"""Governed multi-registry MCP catalog ingestion (row 68).

Bulk-ingest the ~72k-server MCP ecosystem WITHOUT dumping any of it into context: fan out across
**multiple registries** (Official Registry + Glama metaregistry + Smithery + mcp.so + PulseMCP),
**dedupe** by canonical identity (recording *which* registries listed each server = cross-source
provenance/corroboration), then for each unique server **scan-before-index** (`scan_tools` —
poisoning → quarantine) and **pin-before-active** (`sign_manifest` — ETDI attestation). Active
servers are surfaced on demand via `find` (`retrieve_tools` = RAG-MCP / [[Deferred Capability
Loading]]), never bulk-loaded. Composes the existing MCP spine; registry clients are injectable
(no single-source dependency), so the ingest is testable offline.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from madras.mcp.retrieval import retrieve_tools
from madras.mcp.security import scan_tools
from madras.mcp.signing import SignedManifest, generate_keypair, load_private, sign_manifest


@dataclass
class ServerEntry:
    sid: str
    name: str
    url: str = ""  # repo/source URL — the dedup key
    transport: str = "stdio"
    publisher: str = ""
    version: str = "0"
    license: str = ""  # per-server license (provenance; registries don't constrain)
    tools: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])


@runtime_checkable
class RegistryClient(Protocol):
    name: str

    async def list_servers(
        self, *, query: str | None = None, limit: int = 200
    ) -> list[ServerEntry]: ...


@dataclass
class CatalogEntry:
    server: ServerEntry
    status: str  # active | quarantined
    registries: list[str]  # every registry that listed it (cross-source provenance)
    manifest: SignedManifest | None = None
    findings: dict[str, list[str]] = field(
        default_factory=dict[str, list[str]]
    )  # poisoning scan, if quarantined


@dataclass
class IngestReport:
    seen: int  # total listings across registries (pre-dedup)
    unique: int
    active: int
    quarantined: int
    by_registry: dict[str, int]


def _dedup_key(e: ServerEntry) -> str:
    return (e.url or e.name or e.sid).strip().lower().rstrip("/")


@dataclass
class McpCatalog:
    clients: list[RegistryClient]
    publisher_key: str = ""  # Ed25519 priv hex for pinning (auto-generated if empty)
    persist: Callable[[CatalogEntry], Awaitable[None] | None] | None = None  # -> live McpRegistry
    audit: Callable[[dict[str, Any]], None] | None = None
    _entries: dict[str, CatalogEntry] = field(default_factory=dict[str, CatalogEntry], init=False)

    def __post_init__(self) -> None:
        if not self.publisher_key:
            self.publisher_key, _ = generate_keypair()

    def _audit(self, event: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit({"event": event, **kw})

    async def ingest(self, *, query: str | None = None, limit: int = 200) -> IngestReport:
        seen = 0
        by_registry: dict[str, int] = {}
        merged: dict[str, tuple[ServerEntry, list[str]]] = {}

        for client in self.clients:  # multi-registry fan-out
            try:
                entries = await client.list_servers(query=query, limit=limit)
            except Exception as exc:
                self._audit("registry_error", registry=client.name, error=str(exc))
                continue
            by_registry[client.name] = len(entries)
            for e in entries:
                seen += 1
                key = _dedup_key(e)
                if key in merged:  # dedup + merge across registries
                    srv, regs = merged[key]
                    if client.name not in regs:
                        regs.append(client.name)
                    if len(e.tools) > len(srv.tools):  # keep the richest tool manifest
                        srv.tools = e.tools
                    if e.license and not srv.license:
                        srv.license = e.license
                else:
                    merged[key] = (e, [client.name])

        active = quarantined = 0
        priv = load_private(self.publisher_key)
        for key, (srv, regs) in merged.items():
            findings = scan_tools(srv.tools)  # scan-before-index (poisoning)
            if findings:
                self._entries[key] = CatalogEntry(
                    srv,
                    "quarantined",
                    regs,
                    None,
                    {name: [f.pattern for f in flist] for name, flist in findings.items()},
                )
                quarantined += 1
                self._audit("quarantine", sid=srv.sid, registries=regs, tools=list(findings))
            else:
                manifest = sign_manifest(
                    publisher=srv.publisher or "catalog",
                    version=srv.version,
                    tools=srv.tools,
                    private_key=priv,
                )
                self._entries[key] = CatalogEntry(srv, "active", regs, manifest)
                active += 1
                self._audit("active", sid=srv.sid, registries=regs)
            if self.persist is not None:  # durable leg → live McpRegistry
                res = self.persist(self._entries[key])
                if inspect.isawaitable(res):
                    await res

        return IngestReport(seen, len(merged), active, quarantined, by_registry)

    def find(self, query: str, *, k: int = 8) -> list[CatalogEntry]:
        """mcp_find over the ACTIVE catalog — the on-demand subset, never the whole 72k."""
        tagged: list[dict[str, Any]] = []
        for key, entry in self._entries.items():
            if entry.status != "active":
                continue
            for t in entry.server.tools:
                tagged.append({**t, "_key": key})
        out: list[CatalogEntry] = []
        seen: set[str] = set()
        for t in retrieve_tools(tagged, query, k=k * 2):
            key = t.get("_key")
            if key and key not in seen:
                seen.add(key)
                out.append(self._entries[key])
                if len(out) >= k:
                    break
        return out

    def entries(self) -> list[CatalogEntry]:
        return list(self._entries.values())


# public MCP registry base URLs (multi-registry fan-out — point one client per registry)
_REGISTRY_BASES = {
    "official": "https://registry.modelcontextprotocol.io",
    "glama": "https://glama.ai/api/mcp/v1",
    "smithery": "https://registry.smithery.ai",
    "mcp.so": "https://mcp.so/api",
    "pulsemcp": "https://api.pulsemcp.com",
}


class HttpRegistryClient:
    """Adapter for a public MCP registry (Official Registry / Glama / Smithery / mcp.so / PulseMCP).
    The fetch is injectable (or a fake in tests); `connect()` lazy-imports `httpx`. Multi-registry
    fan-out + dedup happen in `McpCatalog` — just point one client per registry."""

    def __init__(
        self,
        name: str,
        *,
        base_url: str = "",
        fetch: Callable[[str | None, int], Awaitable[list[dict[str, Any]]]] | None = None,
        parse: Callable[[dict[str, Any]], ServerEntry] | None = None,
    ) -> None:
        self.name = name
        self._base = base_url or _REGISTRY_BASES.get(name, "")
        self._fetch = fetch
        self._parse = parse or _default_parse

    async def list_servers(
        self, *, query: str | None = None, limit: int = 200
    ) -> list[ServerEntry]:
        fetch = self._fetch or self._default_http_fetch
        return [self._parse(r) for r in await fetch(query, limit)]

    async def _default_http_fetch(self, query: str | None, limit: int) -> list[dict[str, Any]]:
        """Live fetch against the registry's REST API (the official `/v0/servers` shape by default).
        A single GET — not a sweep. Other registries can inject their own `fetch` (auth/shape)."""
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                f"MCP registry fetch needs `httpx`; or inject a `fetch` for '{self.name}'"
            ) from exc
        params: dict[str, Any] = {"limit": min(limit, 100)}
        if query:
            params["search"] = query
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"{self._base}/v0/servers", params=params)
            resp.raise_for_status()
            data: Any = resp.json()
        if isinstance(data, dict):
            data = cast("dict[str, Any]", data)
            return list(data.get("servers") or data.get("results") or data.get("data") or [])
        return cast("list[Any]", data) if isinstance(data, list) else []


def _default_parse(row: dict[str, Any]) -> ServerEntry:
    nested = row.get("server")
    srv: dict[str, Any] = cast("dict[str, Any]", nested) if isinstance(nested, dict) else row
    repo_raw = srv.get("repository")
    repo: dict[str, Any] = cast("dict[str, Any]", repo_raw) if isinstance(repo_raw, dict) else {}
    remotes_raw = srv.get("remotes")
    remotes: list[Any] = cast("list[Any]", remotes_raw) if isinstance(remotes_raw, list) else []
    first_remote: Any = remotes[0] if remotes else None
    remote_url = ""
    if isinstance(first_remote, dict):
        remote_url = cast("dict[str, Any]", first_remote).get("url") or ""
    url: Any = repo.get("url") or remote_url or srv.get("url", "")
    version = srv.get("version")
    if isinstance(srv.get("version_detail"), dict):
        version = srv["version_detail"].get("version", version)
    return ServerEntry(
        sid=str(srv.get("id") or srv.get("name", "")),
        name=str(srv.get("name", "")),
        url=str(url),
        publisher=str(srv.get("publisher") or srv.get("title", "")),
        version=str(version or "0"),
        license=str(srv.get("license", "")),
        tools=list(srv.get("tools", [])),
    )
