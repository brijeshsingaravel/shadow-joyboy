"""Production eval / observability — continuous scoring on LIVE traffic (the Opik surface).

Backend finalized: **Opik** (comet-ml/opik, MIT) — self-hostable LLM observability + eval, with a
native LiteLLM integration (Madras already routes through LiteLLM). Every production trace
(trajectory + the 8 eval-dimension signals + cost/latency) is emitted to an **injectable
`EvalSink`** (Opik adapter; Langfuse/OTel behind the same interface), and **online eval rules**
score live traffic — flag/alert when a dimension breaches a threshold. Sink-agnostic + injectable →
pure/deterministic to build; the live Opik wiring is a thin adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass
class ProductionTrace:
    id: str
    model: str = ""
    input: str = ""
    output: str = ""
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    signals: dict[str, float] = field(default_factory=dict[str, float])  # dimension -> score (0..1)
    metadata: dict[str, Any] = field(default_factory=dict[str, Any])


@runtime_checkable
class EvalSink(Protocol):
    async def emit(self, trace: ProductionTrace) -> None: ...


@dataclass
class RuleVerdict:
    rule: str
    triggered: bool
    dimension: str
    score: float | None
    action: str
    detail: str = ""


@dataclass
class OnlineEvalRule:
    dimension: str
    min_score: float  # trigger when the live score < min_score
    action: str = "flag"  # flag | alert | block
    name: str = ""

    def check(self, trace: ProductionTrace) -> RuleVerdict:
        nm = self.name or f"{self.dimension}>={self.min_score}"
        score = trace.signals.get(self.dimension)
        if score is None:
            return RuleVerdict(nm, False, self.dimension, None, self.action, "dimension absent")
        triggered = score < self.min_score
        detail = f"{score:.2f} < {self.min_score}" if triggered else f"{score:.2f} ok"
        return RuleVerdict(nm, triggered, self.dimension, score, self.action, detail)


@dataclass
class ObserveResult:
    emitted: bool
    triggered: list[RuleVerdict] = field(default_factory=list[RuleVerdict])


@dataclass
class ProductionEval:
    sink: EvalSink | None = None
    rules: list[OnlineEvalRule] = field(default_factory=list[OnlineEvalRule])
    audit: Callable[[dict[str, Any]], None] | None = None

    async def observe(self, trace: ProductionTrace) -> ObserveResult:
        """Score a live trace against the online rules and emit it to the sink."""
        triggered = [v for v in (r.check(trace) for r in self.rules) if v.triggered]
        if self.audit is not None:
            self.audit({"op": "observe", "trace": trace.id, "triggered": len(triggered)})
        emitted = False
        if self.sink is not None:
            await self.sink.emit(trace)
            emitted = True
        return ObserveResult(emitted=emitted, triggered=triggered)


class OpikSink:
    """Adapter over Opik (comet-ml/opik, MIT). The Opik client is injected (or a fake in tests);
    `connect()` lazy-imports the optional `opik` SDK. Live wiring uses a self-hosted Opik (or the
    LiteLLM→Opik integration) and logs the trace + the 8 dimension scores as metadata."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(cls, client_factory: Callable[[], Any] | None = None) -> OpikSink:
        if client_factory is not None:
            return cls(client_factory())
        try:
            import opik  # noqa: F401  # type: ignore[reportMissingImports, reportUnusedImport]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "opik is not installed — `pip install opik` (MIT) + a self-hosted Opik (or the "
                "LiteLLM→Opik integration) to wire the live production-eval sink"
            ) from exc
        raise RuntimeError("provide a configured Opik client via client_factory")

    async def emit(self, trace: ProductionTrace) -> None:
        self._client.trace(
            name=trace.id,
            input=trace.input,
            output=trace.output,
            metadata={
                "model": trace.model,
                "cost_usd": trace.cost_usd,
                "latency_ms": trace.latency_ms,
                **trace.signals,
            },
        )
