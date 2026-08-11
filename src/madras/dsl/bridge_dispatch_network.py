"""Phase U -- the network transport's dispatcher: takes a real `BridgeManifest`
(transport=network) and actually calls it, generalizing `madras.mcp.client`'s existing governed
MCP session machinery (`open_session`/`result_text`) into a manifest-driven entrypoint, exactly
the same "reuse, don't reinvent" move `dispatch_in_process` (`bridge_resolve.py`) already made
for G8/N5.

The manifest's `NetworkInterface.server` (a `StdioServerDescriptor`/`HttpSseServerDescriptor`,
matching `resolve`'s own discriminated-union shape) is self-contained -- it alone is enough to
open a real session, no registry/DB lookup required, so a manifest can be dispatched standalone
(useful for a marketplace listing or a test) as well as through the full governed registry path
`mcp_invoke` already provides for already-registered servers.
"""

from __future__ import annotations

from typing import Any

from madras.mcp.client import open_session, result_text
from madras.models.bridge_manifest import BridgeManifest, ServerDescriptor, Transport


def _server_dict(server: ServerDescriptor) -> dict[str, Any]:
    """`open_session` takes a plain `dict` server record (the registry's own storage shape) --
    translate the manifest's typed descriptor into that same shape, no new client code needed."""
    if server.transport == "stdio":
        return {"transport": "stdio", "command": server.command, "args": list(server.args)}
    return {"transport": "http", "url": server.url}


async def dispatch_network(manifest: BridgeManifest, args: dict[str, Any] | None = None) -> str:
    """Resolve `manifest.network_interface` to a real MCP session and actually call
    `method` with `args`, returning the real result text."""
    if manifest.transport is not Transport.NETWORK:
        raise ValueError(
            f"dispatch_network only handles transport=network, got {manifest.transport!r}"
        )
    iface = manifest.network_interface
    assert iface is not None  # enforced by BridgeManifest's own transport/interface validator

    server = _server_dict(iface.server)
    async with open_session(server) as session:
        result = await session.call_tool(iface.method, args or {})
    return result_text(result)


__all__ = ["dispatch_network"]
