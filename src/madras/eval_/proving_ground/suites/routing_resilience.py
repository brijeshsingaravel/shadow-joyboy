"""Zero-cost Routing resilience — the deterministic conformance suite (C2, framework-10x Part C).

Mirrors `identity_boundary.py` (C1) exactly: the 5 s33 routing capabilities (`route_capable` /
`apply_policy` / `auto_route` / `ProviderHealth` / `run_with_fallback`) are pure, deterministic
pipeline code — a task doesn't "decide" whether a fallback chain retries correctly, the code does,
before any agent turn even starts. So every case is a direct adversarial (or happy-path) call into
the real module. Zero LLM tokens spent.

Composes the existing engine (same `Scenario`-shaped JSON + partition convention + `Suite.run()`
external-suite dispatch point) exactly like C1 — no engine change.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite
from madras.llm.auto_router import auto_route
from madras.llm.capability_routing import CapabilityReq, exclusion_reasons, route_capable
from madras.llm.fallback_chain import run_with_fallback
from madras.llm.model_catalog import ModelInfo
from madras.llm.provider_health import ProviderHealth
from madras.llm.routing_policy import RoutingPolicy, apply_policy

DATA_DIR = Path(__file__).resolve().parent / "routing_resilience" / "data"
_FEATURES = [
    "capability_routing",
    "routing_policy",
    "auto_router",
    "provider_health",
    "fallback_chain",
]

# a small, consistent fixture pool shared across cases (pure data, no I/O)
_WEAK = ModelInfo(
    id="free-weak",
    provider="ollama",
    context_window=8_000,
    input_cost=0.0,
    output_cost=0.0,
    free=True,
)
_STRONG = ModelInfo(
    id="free-strong",
    provider="together",
    context_window=128_000,
    input_cost=0.0,
    output_cost=0.0,
    free=True,
    tool_call=True,
    structured_output=True,
    reasoning=True,
)
_CHEAP = ModelInfo(
    id="paid-cheap",
    provider="openai",
    context_window=64_000,
    input_cost=0.0000005,
    output_cost=0.0000015,
    free=False,
    tool_call=True,
    structured_output=True,
)
_EXPENSIVE = ModelInfo(
    id="paid-expensive",
    provider="anthropic",
    context_window=200_000,
    input_cost=0.000015,
    output_cost=0.000075,
    free=False,
    tool_call=True,
    structured_output=True,
    reasoning=True,
    input_modalities=("text", "image"),
)


def _load_cases(partition: str | None) -> list[dict[str, Any]]:
    files = {
        "public": [DATA_DIR / "public.json"],
        "held_out": [DATA_DIR / "held_out.json"],
        None: [DATA_DIR / "public.json", DATA_DIR / "held_out.json"],
    }[partition]
    rows: list[dict[str, Any]] = []
    for f in files:
        if f.exists():
            rows.extend(json.loads(f.read_text(encoding="utf-8")))
    return rows


# ---------------------------------------------------------------------------
# Per-module executors — each runs the REAL routing code against the case's
# adversarial (or happy-path) setup and returns (passed, detail).
# ---------------------------------------------------------------------------


def _exec_capability_routing(attack: str) -> tuple[bool, str]:
    pool = [_WEAK, _STRONG]
    if attack == "gate_excludes_incapable":
        req = CapabilityReq(tools=True)
        kept = route_capable(req, pool)
        ok = _WEAK not in kept and "no tool_call" in exclusion_reasons(req, _WEAK)
        return ok, f"kept={[m.id for m in kept]} reasons={exclusion_reasons(req, _WEAK)}"
    if attack == "gate_keeps_capable":
        req = CapabilityReq(tools=True)
        kept = route_capable(req, pool)
        return _STRONG in kept, f"kept={[m.id for m in kept]}"
    if attack == "min_context":
        req = CapabilityReq(min_context=100_000)
        kept = route_capable(req, [_WEAK, _EXPENSIVE])
        ok = _WEAK not in kept and _EXPENSIVE in kept
        return ok, f"kept={[m.id for m in kept]}"
    if attack == "modality_gate":
        req = CapabilityReq(modality="image")
        kept = route_capable(req, [_STRONG, _EXPENSIVE])
        ok = _STRONG not in kept and _EXPENSIVE in kept
        return ok, f"kept={[m.id for m in kept]}"
    return False, f"unknown attack {attack!r}"


def _exec_routing_policy(attack: str) -> tuple[bool, str]:
    pool = [_WEAK, _STRONG, _CHEAP, _EXPENSIVE]
    if attack == "free_only":
        out = apply_policy(RoutingPolicy(free_only=True), pool)
        ok = all(m.free for m in out) and _WEAK in out and _STRONG in out
        return ok, f"chain={[m.id for m in out]}"
    if attack == "ignore_denylist":
        out = apply_policy(RoutingPolicy(ignore=("openai",)), pool)
        ok = all(m.provider != "openai" for m in out)
        return ok, f"chain={[m.id for m in out]}"
    if attack == "max_price":
        out = apply_policy(RoutingPolicy(max_price=0.000001), pool)
        ok = _EXPENSIVE not in out and _CHEAP in out and _WEAK in out
        return ok, f"chain={[m.id for m in out]}"
    if attack == "sort_price":
        out = apply_policy(RoutingPolicy(sort="price"), pool)
        costs = [m.input_cost for m in out]
        return costs == sorted(costs), f"order={[m.id for m in out]}"
    if attack == "provider_order":
        out = apply_policy(RoutingPolicy(order=("anthropic", "openai")), pool)
        anthropic_idx = next(i for i, m in enumerate(out) if m.provider == "anthropic")
        others_idx = [i for i, m in enumerate(out) if m.provider not in {"anthropic", "openai"}]
        ok = all(anthropic_idx < i for i in others_idx) or not others_idx
        return ok, f"order={[m.id for m in out]}"
    if attack == "allow_fallbacks_false":
        out = apply_policy(RoutingPolicy(allow_fallbacks=False), pool)
        return len(out) == 1, f"chain={[m.id for m in out]}"
    return False, f"unknown attack {attack!r}"


def _exec_auto_router(attack: str) -> tuple[bool, str]:
    pool = [_WEAK, _EXPENSIVE]  # weak=cheap/low-quality, expensive=strong/high-quality
    if attack == "tradeoff_quality":
        result = auto_route(pool, tradeoff=0)
        return result.model is _EXPENSIVE, f"picked={result.model and result.model.id}"
    if attack == "tradeoff_cheap":
        result = auto_route(pool, tradeoff=10)
        return result.model is _WEAK, f"picked={result.model and result.model.id}"
    if attack == "gate_applied":
        result = auto_route([_WEAK, _STRONG], tradeoff=0, require=CapabilityReq(tools=True))
        ranked_ids = [s.model.id for s in result.ranked]
        ok = result.model is _STRONG and all(s.model is not _WEAK for s in result.ranked)
        return ok, f"picked={result.model and result.model.id} ranked={ranked_ids}"
    if attack == "empty_pool":
        result = auto_route([_WEAK], tradeoff=0, require=CapabilityReq(modality="image"))
        return result.model is None, f"picked={result.model}"
    return False, f"unknown attack {attack!r}"


def _exec_provider_health(attack: str) -> tuple[bool, str]:
    if attack == "default_healthy":
        health = ProviderHealth()
        up = health.uptime("openai", now=100.0)
        return up == 1.0, f"uptime={up}"
    if attack == "failures_lower_uptime":
        health = ProviderHealth()
        for i in range(3):
            health.record("openai", False, now=100.0 + i)
        up = health.uptime("openai", now=103.0)
        return up < 1.0, f"uptime={up}"
    if attack == "failure_ages_out":
        health = ProviderHealth(window_s=30.0)
        health.record("openai", False, now=100.0)
        up = health.uptime("openai", now=200.0)  # 100s later, outside the 30s window
        return up == 1.0, f"uptime_after_aging={up}"
    if attack == "rank_deprioritizes":
        health = ProviderHealth()
        health.record("openai", False, now=100.0)
        m_openai = ModelInfo(id="m-openai", provider="openai", input_cost=0.0000005)
        m_azure = ModelInfo(id="m-azure", provider="azure", input_cost=0.0000005)
        ranked = health.rank_models([m_openai, m_azure], now=100.5)
        return ranked[0].provider == "azure", f"ranked={[m.provider for m in ranked]}"
    return False, f"unknown attack {attack!r}"


class _FallbackError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"error {status_code}")
        self.status_code = status_code


def _exec_fallback_chain(attack: str) -> tuple[bool, str]:
    calls: list[str] = []

    def _ok(model: str) -> str:
        calls.append(model)
        return f"response-from-{model}"

    def _fail_then_ok(model: str) -> str:
        calls.append(model)
        if model == "model-a":
            raise _FallbackError(429)
        return f"response-from-{model}"

    def _always_transient(model: str) -> str:
        calls.append(model)
        raise _FallbackError(503)

    def _terminal_first(model: str) -> str:
        calls.append(model)
        if model == "model-a":
            raise ValueError("bad request: malformed payload")
        return f"response-from-{model}"  # must never be reached

    if attack == "success_first":
        result = run_with_fallback(["model-a", "model-b"], _ok)
        ok = result.ok and result.model == "model-a" and not result.attempts
        return ok, f"ok={result.ok} model={result.model} attempts={len(result.attempts)}"
    if attack == "transient_falls_through":
        result = run_with_fallback(["model-a", "model-b"], _fail_then_ok)
        ok = result.ok and result.model == "model-b" and len(result.attempts) == 1
        return ok, f"ok={result.ok} model={result.model} attempts={len(result.attempts)}"
    if attack == "empty_chain":
        result = run_with_fallback([], _ok)
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"
    if attack == "all_exhausted":
        result = run_with_fallback(["model-a", "model-b"], _always_transient)
        ok = result.ok is False and len(result.attempts) == 2
        return ok, f"ok={result.ok} attempts={len(result.attempts)}"
    if attack == "terminal_short_circuits":
        result = run_with_fallback(["model-a", "model-b"], _terminal_first)
        ok = result.ok is False and "model-b" not in calls
        return ok, f"ok={result.ok} calls={calls}"
    if attack == "one_attempt_per_model":
        run_with_fallback(["model-a", "model-b", "model-c"], _always_transient)
        ok = calls == ["model-a", "model-b", "model-c"]  # each exactly once, in order
        return ok, f"calls={calls}"
    return False, f"unknown attack {attack!r}"


_EXECUTORS: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "capability_routing": lambda s: _exec_capability_routing(s["attack"]),
    "routing_policy": lambda s: _exec_routing_policy(s["attack"]),
    "auto_router": lambda s: _exec_auto_router(s["attack"]),
    "provider_health": lambda s: _exec_provider_health(s["attack"]),
    "fallback_chain": lambda s: _exec_fallback_chain(s["attack"]),
}


def run_case(row: dict[str, Any]) -> dict[str, Any]:
    """Execute one adversarial/happy-path case against the real routing module. Pure, hermetic."""
    setup = row["setup"]
    executor = _EXECUTORS[setup["module"]]
    t0 = time.perf_counter()
    try:
        passed, detail = executor(setup)
    except Exception as exc:  # a raising module is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": row["id"],
        "suite_id": "routing_resilience_conformance",
        "benchmark_family": "routing_resilience_conformance",
        "features": row.get("features", []),
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "security_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class RoutingResilienceConformanceSuite(Suite):
    id: str = "routing_resilience_conformance"
    name: str = "Zero-cost Routing resilience — deterministic conformance"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct adversarial + "
        "happy-path calls into route_capable/apply_policy/auto_route/"
        "ProviderHealth/run_with_fallback. Public + held_out partitions."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    partition: str | None = None  # None = both partitions (the official Index view)

    def load_cases(self) -> list[Case]:
        """Lightweight coverage-stub Cases (one per module), matching the convention every other
        external suite (tau2/swebench/identity_boundary/...) follows — this suite self-drives via
        `run()`, so these are not executed through the governed runner."""
        return [
            Case(
                id=f"routing_resilience-{module}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=[module],
                tools=[],
                prompt=f"{self.name}: {module} conformance cases (external; zero-LLM)",
            )
            for module in sorted(_EXECUTORS)
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency  # deterministic + zero-cost: irrelevant, no LLM call at all
        return [run_case(row) for row in _load_cases(self.partition)]
