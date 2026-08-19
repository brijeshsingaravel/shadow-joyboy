"""OpenRouter HTTP backend.

OpenRouter exposes an OpenAI-compatible /chat/completions endpoint and
routes to whatever provider/model is asked for via "provider/model" strings.
Docs: https://openrouter.ai/docs
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from madras.llm.gateway import LLMBackend, LLMRequest, LLMResponse, ToolCall

OPENROUTER_BASE = "https://openrouter.ai/api/v1"

# OpenRouter reads these two headers for its public app rankings, so whatever is sent here
# is what your traffic is credited to. It used to be hard-coded to the author's hosted
# site, which meant every self-hoster's calls were quietly attributed to a deployment that
# is not theirs. Defaults to this repository; override per deployment.
OPENROUTER_REFERER = "https://github.com/brijeshsingaravel/shadow-joyboy"


class OpenRouterBackend(LLMBackend):
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = OPENROUTER_BASE,
        referer: str = OPENROUTER_REFERER,
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("OpenRouter api_key is required")
        self._api_key = api_key
        self._base_url = base_url
        self._referer = referer
        self._timeout = timeout
        self._transport = transport

    async def complete(self, req: LLMRequest) -> LLMResponse:
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
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "HTTP-Referer": self._referer,
            "X-Title": "Madras AI",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
            r = await client.post(
                f"{self._base_url}/chat/completions",
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
        # OpenRouter returns cost in `usage.cost` (USD) when generation.activity is enabled.
        cost = float(usage.get("cost", 0.0))

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
