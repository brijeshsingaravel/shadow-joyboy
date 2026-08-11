"""CodeAct execution mode — S3 (the runnable action) + the localhost RPC HTTP wrapper.

``run_codeact`` is the whole action: build the governed ToolBridge (S1), serve it on a
127.0.0.1 ephemeral port, generate the sandbox prelude (S2), write ``prelude + code`` into the
sandbox and run it, then parse the structured ``result()`` and stdout back out. The sandbox's
code reaches tools ONLY through the authenticated, allowlisted, rank-gated bridge — so a CodeAct
action has exactly the same governance surface as a textual tool-call.

Network posture: the RPC binds 127.0.0.1 with a per-call random token. With LocalSandbox the
code runs on the host so localhost reaches it directly; DockerSandbox maps the same port via
host.docker.internal and restricts egress to it (S4 governance).
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

from madras.codeact.prelude import RESULT_MARKER, generate_prelude
from madras.codeact.rpc_server import ToolBridge
from madras.eval_.emitter import emit_action_signals
from madras.models.agent_config import Rank
from madras.tools.registry import GovernedExecutor, ToolRegistry


@dataclass
class CodeActResult:
    ok: bool  # the sandbox process exited 0
    result: Any = None  # the value passed to result() in the code, if any
    stdout: str = ""
    error: str | None = None
    tool_calls: list[str] = field(default_factory=list[str])  # tool names the code invoked


class _BridgeServer:
    """Serves one ToolBridge over POST /tool on a 127.0.0.1 ephemeral port (async ctx)."""

    def __init__(self, bridge: ToolBridge) -> None:
        self._bridge = bridge
        self._runner: web.AppRunner | None = None
        self.url = ""
        self.calls: list[str] = []

    async def _handle(self, request: web.Request) -> web.Response:
        data = await request.json()
        name = str(data.get("name", ""))
        self.calls.append(name)
        resp = await self._bridge.dispatch(
            token=str(data.get("token", "")), name=name, args=data.get("args") or {}
        )
        return web.json_response(resp.body, status=resp.status)

    async def __aenter__(self) -> _BridgeServer:
        app = web.Application()
        app.router.add_post("/tool", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = self._runner.addresses[0][1]
        self.url = f"http://127.0.0.1:{port}/tool"
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._runner is not None:
            await self._runner.cleanup()


async def run_codeact(
    *,
    code: str,
    registry: ToolRegistry,
    allowlist: set[str],
    agent_name: str,
    session_id: str,
    agent_rank: Rank,
    sandbox: Any,
    token: str | None = None,
    audit: Any = None,
    emit: Any = emit_action_signals,
    timeout: float = 60.0,
) -> CodeActResult:
    """Run one CodeAct action: the agent's Python (``code``) executes in the sandbox and calls its
    allowlisted tools through the governed RPC bridge. Returns the parsed result + stdout."""
    token = token or secrets.token_urlsafe(24)
    executor = GovernedExecutor(registry=registry, audit=audit, emit=emit)
    bridge = ToolBridge(
        executor=executor,
        allowlist=set(allowlist),
        token=token,
        agent_name=agent_name,
        session_id=session_id,
        agent_rank=agent_rank,
    )
    async with _BridgeServer(bridge) as srv:
        prelude = generate_prelude(allowlist=set(allowlist), token=token, endpoint=srv.url)
        await sandbox.write_file("__madras_codeact.py", prelude + "\n" + code)
        res = await sandbox.run_command("python __madras_codeact.py", timeout=timeout)
        calls = list(srv.calls)

    result: Any = None
    for line in (res.stdout or "").splitlines():
        if line.startswith(RESULT_MARKER):
            try:
                result = json.loads(line[len(RESULT_MARKER) :])
            except json.JSONDecodeError:
                result = None
    return CodeActResult(
        ok=res.ok,
        result=result,
        stdout=res.stdout or "",
        error=(res.error or res.stderr or None) if not res.ok else None,
        tool_calls=calls,
    )
