"""LiteLLM proxy backend.

Points at a LiteLLM proxy, if you run one (OpenAI-compatible /v1/chat/completions)
running in the local Docker stack. Lets Madras take real LLM turns against
locally-routed models (gemini, llama, deepseek, qwen, ...) without spending on
OpenRouter — used for live verification when OpenRouter credits are unavailable.

Base URL + key come from the master vault (LITELLM_BASE_URL, LITELLM_MASTER_KEY).
Note: locally-routed/free models report no per-call cost, so `cost_usd` is 0.0;
the cost-curve exit criterion still requires a metered paid provider.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from madras.llm.gateway import LLMBackend, LLMRequest, LLMResponse, ToolCall


class LiteLLMBackend(LLMBackend):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "http://localhost:4000",
        timeout: float = 90.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        # The key check is deferred to complete() (below), NOT enforced here: constructing a
        # gateway must never require a live key. The server builds a per-request gateway
        # eagerly (app.py::_select_gateway) even on paths where the runner is mocked/short-
        # circuited and the backend is never actually called (every /v1/tasks test) -- a
        # __init__-time raise turned "no LiteLLM key in CI" into 14 spurious 500s on routes
        # that make zero LLM calls. A genuine call without a key still fails clearly, in
        # complete(), where the key is actually needed.
        self._api_key = api_key
        # Vault may set the base URL with or without a trailing /v1; normalize to the
        # root so we always append exactly one /v1/chat/completions.
        root = base_url.rstrip("/")
        if root.endswith("/v1"):
            root = root[: -len("/v1")]
        self._base_url = root
        self._timeout = timeout
        self._transport = transport

    async def complete(self, req: LLMRequest) -> LLMResponse:
        if not self._api_key:
            raise ValueError("LiteLLM api_key is required")
        start = time.perf_counter()
        payload: dict[str, Any] = {
            "model": req.model,
            "messages": req.messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
        }
        if req.tools:
            payload["tools"] = req.tools
        if req.seed is not None:
            payload["seed"] = req.seed
        if req.metadata:
            # LiteLLM's Langfuse callback reads session_id/tags from here to
            # group traces by session instead of one flat trace per call.
            # `existing_trace_id` nests this generation as a child observation
            # under the turn's own trace (obs/langfuse_client.py's
            # start_turn_trace) instead of creating a separate flat trace per
            # LLM call — the same trace that tool-call spans + eval scores land on.
            session_id = req.metadata.get("session_id")
            agent_name = req.metadata.get("agent_name")
            trace_id = req.metadata.get("langfuse_trace_id")
            payload["metadata"] = {
                **({"session_id": session_id} if session_id else {}),
                **({"existing_trace_id": trace_id} if trace_id else {}),
                "tags": [t for t in (agent_name,) if t],
            }
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            r = await client.post(
                f"{self._base_url}/v1/chat/completions",
                json=payload,
                headers=headers,
            )
            r.raise_for_status()
            data = r.json()

        latency_ms = (time.perf_counter() - start) * 1000.0
        choice = data["choices"][0]
        text = choice["message"].get("content") or ""
        usage = data.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))
        # LiteLLM returns usage.cost only for metered providers; None/absent for local models.
        cost = float(usage.get("cost") or 0.0)

        raw_tool_calls: list[dict[str, Any]] = choice["message"].get("tool_calls") or []
        tool_calls = [
            ToolCall(
                id=tc["id"],
                name=tc["function"]["name"],
                arguments=tc["function"]["arguments"],
            )
            for tc in raw_tool_calls
        ]

        return LLMResponse(
            text=text,
            model=req.model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=cost,
            latency_ms=latency_ms,
            raw=data,
            tool_calls=tool_calls,
        )
