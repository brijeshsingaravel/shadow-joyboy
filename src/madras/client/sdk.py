"""MadrasClient — a thin, typed client over the headless Madras runtime server.

One server, many clients: the runtime (`server/app.py`) is a FastAPI app exposing an
OpenAI-compatible chat API (`/v1/chat/completions`) plus health/models/config/sessions/approve.
This client wraps those endpoints so any frontend (TUI, desktop, programmatic) talks to the
runtime uniformly. Because the chat API is OpenAI-wire-compatible (`choices[0].message.content`),
any OpenAI SDK — including the Vercel AI SDK — is already a Madras client too.

`httpx` is injectable (`client=`) so the same class drives a real server over the network, an
in-process app via `ASGITransport`, or a `MockTransport` in tests.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

Message = dict[str, str]  # {"role": "...", "content": "..."}


@dataclass
class StreamEvent:
    """One governed event off /v1/chat/stream: type ∈ {tool_call, tool_result, answer,
    approval_required, done} + its payload."""

    type: str
    data: dict[str, Any] = field(default_factory=dict[str, Any])


def _parse_sse(line: str) -> dict[str, Any] | None:
    """Parse one SSE 'data: {json}' line; None for keep-alives / non-data lines."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:].strip()
    if not payload:
        return None
    try:
        obj = json.loads(payload)
    except (json.JSONDecodeError, ValueError):
        return None
    return cast("dict[str, Any]", obj) if isinstance(obj, dict) else None


class MadrasClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3050",
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._client = client
        self._owns = client is None
        self._timeout = timeout

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base, timeout=self._timeout)
        return self._client

    async def _get(self, path: str) -> Any:
        r = await self._c().get(path)
        r.raise_for_status()
        return r.json()

    async def health(self) -> dict[str, Any]:
        return await self._get("/healthz")

    async def models(self) -> dict[str, Any]:
        return await self._get("/models")

    async def config(self) -> dict[str, Any]:
        return await self._get("/config")

    async def sessions(self) -> Any:
        return await self._get("/sessions")

    async def chat(
        self,
        messages: list[Message],
        *,
        model: str = "llama-70b",
        session_id: str = "cockpit-001",
        backend: str = "local",
        tools_on: bool = False,
        toolsets: list[str] | None = None,
        mode: str = "default",
    ) -> dict[str, Any]:
        """Send an OpenAI-shaped chat request to the headless server; return the full response."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "session_id": session_id,
            "backend": backend,
            "tools_on": tools_on,
            "mode": mode,
        }
        if toolsets is not None:
            payload["toolsets"] = toolsets
        r = await self._c().post("/v1/chat/completions", json=payload)
        r.raise_for_status()
        return r.json()

    async def chat_text(self, prompt: str, **kwargs: Any) -> str:
        """Convenience: send a single user message, return the assistant text."""
        resp = await self.chat([{"role": "user", "content": prompt}], **kwargs)
        return resp["choices"][0]["message"]["content"]

    async def stream(
        self,
        messages: list[Message],
        *,
        model: str = "llama-70b",
        session_id: str = "cockpit-001",
        backend: str = "local",
        tools_on: bool = False,
        toolsets: list[str] | None = None,
        mode: str = "default",
    ) -> AsyncIterator[StreamEvent]:
        """Stream governed events off /v1/chat/stream (tool_call/tool_result/answer/
        approval_required/done) — the agent-as-a-service surface."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "session_id": session_id,
            "backend": backend,
            "tools_on": tools_on,
            "mode": mode,
        }
        if toolsets is not None:
            payload["toolsets"] = toolsets
        async with self._c().stream("POST", "/v1/chat/stream", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                obj = _parse_sse(line)
                if obj is not None:
                    yield StreamEvent(type=str(obj.get("type", "")), data=obj)

    async def run(
        self,
        messages: list[Message],
        *,
        on_approval: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Consume a stream to completion. Collects events; if an approval is required and
        `on_approval(event) -> 'allow'|'deny'` is given, approves and continues. Returns
        {answer, events, madras, approvals}."""
        answer_parts: list[str] = []
        events: list[StreamEvent] = []
        madras: dict[str, Any] = {}
        approvals: list[str] = []
        async for ev in self.stream(messages, **kwargs):
            events.append(ev)
            if ev.type == "answer":
                answer_parts.append(str(ev.data.get("text", "")))
            elif ev.type == "done":
                madras = ev.data.get("madras", {})
            elif ev.type == "approval_required" and on_approval is not None:
                approval_id = str(ev.data.get("approval_id", ""))
                decision = on_approval(ev)
                if approval_id and decision in ("allow", "deny"):
                    await self.approve(approval_id, decision)
                    approvals.append(approval_id)
        return {
            "answer": "".join(answer_parts),
            "events": events,
            "madras": madras,
            "approvals": approvals,
        }

    async def approve(
        self,
        approval_id: str,
        decision: str,
        *,
        scope: str = "once",
    ) -> dict[str, Any]:
        r = await self._c().post(
            "/approve", json={"approval_id": approval_id, "decision": decision, "scope": scope}
        )
        r.raise_for_status()
        return r.json()

    async def aclose(self) -> None:
        if self._owns and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> MadrasClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()
