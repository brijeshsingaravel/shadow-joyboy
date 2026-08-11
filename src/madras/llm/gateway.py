"""LLM gateway — provider-agnostic interface.

Doctrine: every LLM call in Madras goes through this gateway. Backends
(OpenRouter, Anthropic direct, LiteLLM proxy) implement the protocol.
This is the single seam for tracing (Langfuse), cost cascading, retries.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class LLMRequest:
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int = 1024
    temperature: float = 0.7
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])
    tools: list[dict[str, Any]] | None = None
    seed: int | None = None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON string


@dataclass
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    raw: dict[str, Any] = field(default_factory=dict[str, Any])
    tool_calls: list[ToolCall] = field(default_factory=list[ToolCall])


class LLMBackend(abc.ABC):
    @abc.abstractmethod
    async def complete(self, req: LLMRequest) -> LLMResponse: ...


class FakeBackend(LLMBackend):
    """Deterministic fake used in unit tests — never makes a network call."""

    def __init__(
        self,
        *,
        response: str = "fake response",
        input_tokens: int = 5,
        output_tokens: int = 2,
        cost_usd: float = 0.0001,
    ) -> None:
        self._response = response
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self._cost_usd = cost_usd

    async def complete(self, req: LLMRequest) -> LLMResponse:
        start = time.perf_counter()
        latency_ms = (time.perf_counter() - start) * 1000.0
        return LLMResponse(
            text=self._response,
            model=req.model,
            input_tokens=self._input_tokens,
            output_tokens=self._output_tokens,
            cost_usd=self._cost_usd,
            latency_ms=max(latency_ms, 0.001),
        )


class LLMGateway:
    """Single entry point for every LLM call.

    Phase 1 features: backend dispatch. Phase 2 adds Langfuse tracing +
    retries + cost cascading. The interface here is the seam.
    """

    def __init__(self, *, backend: LLMBackend) -> None:
        self._backend = backend

    async def complete(self, req: LLMRequest) -> LLMResponse:
        # Emit one OTel gen_ai span per call when tracing is available; degrade to a plain
        # call if the obs/otel packages aren't installed. A missing tracer must NEVER take
        # down the LLM path (e.g. a minimal container without opentelemetry).
        try:
            from opentelemetry.trace import Status, StatusCode

            from madras.obs import tracing as _t
        except Exception:
            return await self._backend.complete(req)

        with _t.get_tracer().start_as_current_span(f"gen_ai.chat {req.model}") as span:
            span.set_attribute(_t.GEN_AI_OPERATION_NAME, "chat")
            span.set_attribute(_t.GEN_AI_SYSTEM, type(self._backend).__name__)
            span.set_attribute(_t.GEN_AI_REQUEST_MODEL, req.model)
            span.set_attribute(_t.GEN_AI_REQUEST_MAX_TOKENS, req.max_tokens)
            span.set_attribute(_t.GEN_AI_REQUEST_TEMPERATURE, req.temperature)
            span.set_attribute(_t.GEN_AI_REQUEST_MESSAGE_COUNT, len(req.messages))
            try:
                resp = await self._backend.complete(req)
            except Exception as exc:
                span.set_status(Status(StatusCode.ERROR, str(exc)))
                span.record_exception(exc)
                raise
            span.set_attribute(_t.GEN_AI_RESPONSE_MODEL, resp.model)
            span.set_attribute(_t.GEN_AI_USAGE_INPUT_TOKENS, resp.input_tokens)
            span.set_attribute(_t.GEN_AI_USAGE_OUTPUT_TOKENS, resp.output_tokens)
            span.set_attribute(_t.GEN_AI_TOOL_CALLS, len(resp.tool_calls))
            span.set_attribute(_t.MADRAS_COST_USD, resp.cost_usd)
            span.set_attribute(_t.MADRAS_LATENCY_MS, resp.latency_ms)
            return resp
