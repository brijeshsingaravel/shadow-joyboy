"""MCP registry store + governed gateway operations.

Registry persists declarative server defs + their (scanned, pinned) tool manifests
(migration 0016). The gateway functions register a server from a connected client —
SCANNING every tool for poisoning and PINNING the manifest before trust (quarantine on
hit), RE-VERIFYING the pin on reconnect (rug-pull defense), and gating tool calls on
allowlist + status + per-tool flag. Connection uses the existing MCPClient (stdio in the
sandbox / http), so this is testable against the real stub MCP server.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from madras.mcp.security import manifest_hash, scan_tools, verify_pin

_UPSERT_SERVER = """
INSERT INTO madras_mcp_servers
  (id, name, transport, command, args, url, status, allowlisted, pinned_hash, agent_name,
   created_at, note)
VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12)
ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, transport=EXCLUDED.transport,
  command=EXCLUDED.command, args=EXCLUDED.args, url=EXCLUDED.url, status=EXCLUDED.status,
  allowlisted=EXCLUDED.allowlisted, pinned_hash=EXCLUDED.pinned_hash, note=EXCLUDED.note
"""
_LIST_SERVERS = "SELECT * FROM madras_mcp_servers WHERE agent_name=$1 ORDER BY created_at DESC"
_GET_SERVER = "SELECT * FROM madras_mcp_servers WHERE id=$1"
_SET_STATUS = "UPDATE madras_mcp_servers SET status=$2, note=$3 WHERE id=$1"
_SET_PIN = "UPDATE madras_mcp_servers SET pinned_hash=$2 WHERE id=$1"
_DEL_TOOLS = "DELETE FROM madras_mcp_tools WHERE server_id=$1"
_INS_TOOL = (
    "INSERT INTO madras_mcp_tools (server_id, name, description, schema, flagged) "
    "VALUES ($1,$2,$3,$4::jsonb,$5) ON CONFLICT (server_id, name) DO UPDATE SET "
    "description=EXCLUDED.description, schema=EXCLUDED.schema, flagged=EXCLUDED.flagged"
)
_TOOLS_FOR = "SELECT * FROM madras_mcp_tools WHERE server_id=$1"
_ALL_TOOLS = (
    "SELECT t.* FROM madras_mcp_tools t JOIN madras_mcp_servers s "
    "ON t.server_id=s.id WHERE s.agent_name=$1 AND s.status='active' "
    "AND t.flagged=FALSE"
)


class McpRegistry:
    def __init__(self, *, postgres_url: str, agent_name: str = "shadow") -> None:
        self._url = postgres_url
        self._agent = agent_name
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=4)
        return self._pool

    async def upsert_server(
        self,
        sid: str,
        *,
        name: str,
        transport: str = "stdio",
        command: str = "",
        args: list[str] | None = None,
        url: str = "",
        allowlisted: bool = False,
        created_at: float = 0.0,
    ) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                _UPSERT_SERVER,
                sid,
                name,
                transport,
                command,
                json.dumps(args or []),
                url,
                "active",
                allowlisted,
                "",
                self._agent,
                created_at,
                "",
            )

    async def get_server(self, sid: str) -> dict[str, Any] | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(_GET_SERVER, sid)
        return dict(r) if r else None

    async def list_servers(self) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_LIST_SERVERS, self._agent)
        return [dict(r) for r in rows]

    async def set_status(self, sid: str, status: str, note: str = "") -> None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            await conn.execute(_SET_STATUS, sid, status, note)

    async def _replace_tools(
        self,
        conn: asyncpg.pool.PoolConnectionProxy,
        sid: str,
        tools: list[dict[str, Any]],
        flagged_names: set[str],
    ) -> None:
        await conn.execute(_DEL_TOOLS, sid)
        for t in tools:
            await conn.execute(
                _INS_TOOL,
                sid,
                t["name"],
                t.get("description", ""),
                json.dumps(t.get("schema") or {}),
                t["name"] in flagged_names,
            )

    async def tools_for(self, sid: str) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_TOOLS_FOR, sid)
        return [
            {
                "name": r["name"],
                "description": r["description"],
                "server": sid,
                "schema": (
                    json.loads(r["schema"]) if isinstance(r["schema"], str) else dict(r["schema"])
                ),
                "flagged": r["flagged"],
            }
            for r in rows
        ]

    async def all_tools(self) -> list[dict[str, Any]]:
        """Active, unflagged tools across servers — the catalog RAG-MCP retrieves over."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_ALL_TOOLS, self._agent)
        return [
            {"name": r["name"], "description": r["description"], "server": r["server_id"]}
            for r in rows
        ]

    async def find_server_for_tool(self, tool: str) -> dict[str, Any] | None:
        """Return the active server hosting `tool` (unflagged), or None. Used by the live
        invoker to resolve which server to connect for a tool call."""
        for t in await self.all_tools():
            if t["name"] == tool:
                return await self.get_server(t["server"])
        return None

    # ── governed gateway operations ──────────────────────────────────────────
    async def register_from_client(
        self,
        sid: str,
        client: Any,
        *,
        signed: Any = None,
        trusted_keys: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """List a connected server's tools, SCAN for poisoning, optionally VERIFY the ETDI
        signature (authenticity — a trusted publisher signed this exact manifest), PIN the
        manifest, store. Quarantines on poisoned tools OR a bad/forged/untrusted signature."""
        raw = await client.list_tools()
        tool_list = getattr(raw, "tools", raw)  # real MCP SDK -> ListToolsResult(.tools)
        tools: list[dict[str, Any]] = [
            {
                "name": t.name,
                "description": (t.description or ""),
                "schema": getattr(t, "inputSchema", None) or {},
            }
            for t in tool_list
        ]
        poisoned = scan_tools(tools)
        if poisoned:
            await self.set_status(sid, "quarantined", note=f"poisoned tools: {', '.join(poisoned)}")
            return {"ok": False, "quarantined": True, "poisoned": list(poisoned)}
        # ETDI authenticity gate — only when a signed manifest + trusted keys are supplied.
        if signed is not None and trusted_keys is not None:
            from madras.mcp.signing import verify_signed_manifest

            v = verify_signed_manifest(signed, current_tools=tools, trusted_keys=trusted_keys)
            if not v.ok:
                await self.set_status(sid, "quarantined", note=f"signature: {v.reason}")
                return {"ok": False, "quarantined": True, "signature": v.reason}
        pinned = manifest_hash(tools)
        pool = await self._get_pool()
        async with pool.acquire() as conn, conn.transaction():
            await self._replace_tools(conn, sid, tools, set())
            await conn.execute(_SET_PIN, sid, pinned)
        return {
            "ok": True,
            "tools": len(tools),
            "pinned": pinned,
            "signature": "authentic" if signed is not None else "unsigned",
        }

    async def reverify(self, sid: str, client: Any) -> dict[str, Any]:
        """Re-check a server's live manifest against its pin (rug-pull defense)."""
        server = await self.get_server(sid)
        if server is None:
            return {"ok": False, "error": "unknown server"}
        raw = await client.list_tools()
        tool_list = getattr(raw, "tools", raw)  # real MCP SDK -> ListToolsResult(.tools)
        tools: list[dict[str, Any]] = [
            {
                "name": t.name,
                "description": (t.description or ""),
                "schema": getattr(t, "inputSchema", None) or {},
            }
            for t in tool_list
        ]
        res = verify_pin(server["pinned_hash"], tools)
        if res.drifted:
            await self.set_status(sid, "quarantined", note="manifest drift (possible rug-pull)")
        return {"ok": res.ok, "drifted": res.drifted}

    async def call_tool(self, sid: str, tool: str, args: dict[str, Any], client: Any) -> str:
        """Gated call: server must be active + the tool present + unflagged."""
        server = await self.get_server(sid)
        if server is None or server["status"] != "active":
            raise PermissionError(f"server {sid} is not active")
        names = {t["name"] for t in await self.tools_for(sid) if not t["flagged"]}
        if tool not in names:
            raise PermissionError(f"tool {tool} not available on {sid}")
        return await client.call_tool(tool, args)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
