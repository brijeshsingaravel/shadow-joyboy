"""Agent Client Protocol (ACP) — the JSON-RPC contract editors (Zed/VS Code/JetBrains) use to
drive an agent. A thin, transport-agnostic dispatcher: the host pipes JSON-RPC messages in,
`AcpServer.handle` routes them (`initialize`, `session/new`, `session/prompt`, `session/cancel`)
to an injected prompt handler. Pure + testable; the network/stdio transport wraps this.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

# JSON-RPC error codes
METHOD_NOT_FOUND = -32601
INVALID_REQUEST = -32600
INTERNAL_ERROR = -32603

PromptHandler = Callable[[str, str], Awaitable[str]]  # (session_id, prompt) -> output


def acp_result(req_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def acp_error(req_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


class AcpServer:
    def __init__(
        self, prompt_handler: PromptHandler, *, capabilities: dict[str, Any] | None = None
    ) -> None:
        self._handler = prompt_handler
        self._caps = capabilities or {"promptCapabilities": {"image": False}, "loadSession": False}
        self._sessions: set[str] = set()
        self._cancelled: set[str] = set()

    async def handle(self, request: dict[str, Any]) -> dict[str, Any]:
        req_id = request.get("id")
        method = request.get("method")
        params: dict[str, Any] = request.get("params") or {}
        if not method:
            return acp_error(req_id, INVALID_REQUEST, "missing method")

        if method == "initialize":
            return acp_result(
                req_id,
                {
                    "protocolVersion": "0.1",
                    "agentCapabilities": self._caps,
                },
            )
        if method == "session/new":
            sid = uuid.uuid4().hex
            self._sessions.add(sid)
            return acp_result(req_id, {"sessionId": sid})
        if method == "session/cancel":
            sid = params.get("sessionId", "")
            self._cancelled.add(sid)
            return acp_result(req_id, {"cancelled": sid in self._sessions})
        if method == "session/prompt":
            sid = params.get("sessionId", "")
            if sid not in self._sessions:
                return acp_error(req_id, INVALID_REQUEST, f"unknown session '{sid}'")
            if sid in self._cancelled:
                return acp_result(req_id, {"stopReason": "cancelled"})
            prompt = params.get("prompt", "")
            try:
                output = await self._handler(sid, prompt)
            except Exception as exc:
                return acp_error(req_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}")
            return acp_result(req_id, {"stopReason": "end_turn", "output": output})

        return acp_error(req_id, METHOD_NOT_FOUND, f"unknown method '{method}'")
