"""OpenTelemetry tracing — the single observability seam (OTel GenAI semconv).

Every LLM call goes through ``LLMGateway``; we emit one ``gen_ai`` span there. ``setup_tracing``
configures a global TracerProvider with an OTLP/HTTP exporter when an endpoint is set
(Langfuse/Phoenix/any OTLP collector); otherwise tracing is a **no-op** — spans are cheap and
dropped — so nothing breaks off-infra. Idempotent.

Why here, not per-backend: ``gateway.LLMGateway.complete`` is the doctrinal single seam for all
LLM calls (cost, retries, tracing), so one span there covers every model call uniformly.
"""

from __future__ import annotations

import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# OTel GenAI semantic-convention attribute keys (stable subset) + Madras extras.
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_REQUEST_MAX_TOKENS = "gen_ai.request.max_tokens"
GEN_AI_REQUEST_TEMPERATURE = "gen_ai.request.temperature"
GEN_AI_REQUEST_MESSAGE_COUNT = "gen_ai.request.message_count"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"
GEN_AI_TOOL_CALLS = "gen_ai.response.tool_calls"
MADRAS_COST_USD = "madras.cost_usd"
MADRAS_LATENCY_MS = "madras.latency_ms"

_configured = False


def setup_tracing(*, service_name: str = "madras", endpoint: str | None = None) -> bool:
    """Configure the global tracer provider once. Returns True if an OTLP exporter was wired.

    ``endpoint`` defaults to ``MADRAS_OTLP_ENDPOINT`` (e.g. http://localhost:4318/v1/traces).
    With no endpoint, tracing stays a no-op and this returns False.
    """
    global _configured
    if _configured:
        return True
    endpoint = endpoint or os.environ.get("MADRAS_OTLP_ENDPOINT")
    if not endpoint:
        return False
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _configured = True
    return True


def get_tracer(name: str = "madras") -> trace.Tracer:
    """Return a tracer. Emits no-op spans when no provider is configured (safe off-infra)."""
    return trace.get_tracer(name)
