"""Observability — OpenTelemetry tracing for Madras (the GenAI-semconv seam)."""

from madras.obs.tracing import get_tracer, setup_tracing

__all__ = ["get_tracer", "setup_tracing"]
