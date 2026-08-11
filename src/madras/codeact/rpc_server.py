"""CodeAct tool-RPC bridge — host side (S1).

The secure seam that lets sandboxed CodeAct code call the agent's GOVERNED tools. The sandbox
prelude (S2) POSTs ``{token, name, args}`` to this dispatch; it authenticates with a per-session
token (constant-time), enforces the agent's allowlist, and runs the SAME governed path as a
textual tool-call (``GovernedExecutor``: ASI03 rank-gate + 8-dim eval signal + immutable audit).
No new privilege path — the bridge can only do what the agent could already do textually.

S1 is the dispatch core (auth + allowlist + governed execute), tested without a socket. The thin
localhost HTTP wrapper (bound to 127.0.0.1, sandbox-network-restricted) is wired in S3.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any

from madras.models.agent_config import Rank
from madras.tools.registry import GovernedExecutor, ToolDenied


@dataclass
class BridgeResponse:
    status: int  # 200 ok · 401 unauthorized · 403 not-allowlisted / denied
    body: dict[str, Any]


class ToolBridge:
    """Authenticated, allowlisted, governed dispatch for CodeAct sandbox tool-calls."""

    def __init__(
        self,
        *,
        executor: GovernedExecutor,
        allowlist: set[str],
        token: str,
        agent_name: str,
        session_id: str,
        agent_rank: Rank,
    ) -> None:
        if not token:
            raise ValueError("ToolBridge requires a non-empty session token")
        self._executor = executor
        self._allowlist = set(allowlist)
        self._token = token
        self._agent = agent_name
        self._session = session_id
        self._rank = agent_rank

    def _authed(self, token: str) -> bool:
        return secrets.compare_digest(str(token or ""), self._token)

    async def dispatch(
        self, *, token: str, name: str, args: dict[str, Any] | None = None
    ) -> BridgeResponse:
        """Run one sandbox tool-call through auth -> allowlist -> the governed executor."""
        if not self._authed(token):
            return BridgeResponse(401, {"ok": False, "error": "unauthorized"})
        if name not in self._allowlist:
            return BridgeResponse(403, {"ok": False, "error": f"tool not in allowlist: {name}"})
        try:
            result = await self._executor.execute(
                tool_name=name,
                args=args or {},
                agent_name=self._agent,
                session_id=self._session,
                agent_rank=self._rank,
            )
        except ToolDenied as exc:
            return BridgeResponse(403, {"ok": False, "error": f"denied: {exc}"})
        return BridgeResponse(
            200,
            {
                "ok": result.ok,
                "content": result.content,
                "error": result.error,
                "extras": result.extras,
            },
        )
