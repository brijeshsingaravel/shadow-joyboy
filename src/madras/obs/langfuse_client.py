"""The live per-turn Langfuse client — the single seam for real-time tracing.

Distinct from ``langfuse_share.py`` (which builds a one-shot session replay for
manual sharing). This module wires **every real Shadow turn**: one trace per
session, tool-call spans nested under it (`tools/registry.py::GovernedExecutor`),
LLM generations nested under it via LiteLLM's ``existing_trace_id`` metadata key
(`llm/litellm.py`), and the 8-dim eval signals pushed as Langfuse scores.

Data-leakage prevention (no raw secrets/credentials ever leave the process):
every trace/span input/output/metadata passes through ``mask_madras_data``
before the SDK serializes it — the Langfuse `mask=` hook runs synchronously at
attribute-creation time, so masking happens before anything is queued for
export, not after.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, cast

_configured_client: Any = None
_health_checked = False
_health_ok = False

# Key names that are unconditionally redacted regardless of value shape — tool
# arguments carry JIT credentials (ASI03), and these names are the load-bearing
# signal that a value is one, not a heuristic on the value's shape.
_SENSITIVE_KEY_RE = re.compile(
    r"(password|secret|token|api_?key|credential|bearer|auth|cookie|private_?key)",
    re.IGNORECASE,
)

# Value-shaped secrets: provider API key prefixes (sk-/pk-/gsk_/etc.), bearer
# tokens, and long high-entropy alnum runs that look like keys even under an
# innocuous field name (e.g. a URL query string carrying a token).
_SECRET_VALUE_RE = re.compile(
    r"(sk-[a-zA-Z0-9_-]{10,}|pk-[a-zA-Z0-9_-]{10,}|gsk_[a-zA-Z0-9_-]{10,}"
    r"|Bearer\s+[a-zA-Z0-9._-]{10,}|[a-zA-Z0-9_-]{32,})"
)

REDACTED = "[REDACTED]"


def mask_madras_data(*, data: Any, **_: Any) -> Any:
    """The Langfuse ``mask`` hook — recurses through dicts/lists/strings.

    Runs client-side, synchronously, before export (Langfuse SDK v2 semantics):
    nothing unmasked ever reaches the wire. Two independent passes: sensitive
    *key* names (redact the whole value, whatever it is) and secret-*shaped*
    values (redact even under an innocuous key name).
    """
    if isinstance(data, dict):
        return {
            key: (REDACTED if _SENSITIVE_KEY_RE.search(str(key)) else mask_madras_data(data=value))
            for key, value in cast("dict[Any, Any]", data).items()
        }
    if isinstance(data, list):
        return [mask_madras_data(data=item) for item in cast("list[Any]", data)]
    if isinstance(data, str):
        return _SECRET_VALUE_RE.sub(REDACTED, data)
    return data


def get_live_client() -> Any:
    """Return the process-wide live Langfuse client, or ``None`` if unconfigured
    or unreachable.

    Configured via ``MADRAS_LANGFUSE_PUBLIC_KEY`` / ``MADRAS_LANGFUSE_SECRET_KEY`` /
    ``MADRAS_LANGFUSE_HOST`` (default ``http://localhost:3004``). Degrades to a
    no-op (returns None, callers skip tracing) when unset, or when a one-time
    bounded health check fails — tracing is never a hard dependency for an agent
    turn (or a test run) to proceed, matching the audit-writer's own "degrade
    gracefully" doctrine.
    """
    global _configured_client, _health_checked, _health_ok
    if _configured_client is not None:
        return _configured_client
    # config.py's Settings deliberately never mutates os.environ (so other modules'
    # naive os.getenv() calls can't see leaked vault values) -- read from settings,
    # not os.environ, or this always degrades to None even when fully configured.
    from madras.config import settings

    public_key = settings.langfuse_public_key_local
    secret_key = settings.langfuse_secret_key_local
    if not public_key or not secret_key:
        return None
    # Fast, bounded, once-per-process reachability check -- never let a broken
    # Langfuse instance create a live client at all. The SDK registers its own
    # atexit hook + background consumer thread at client-CREATION time (not at
    # flush time); once created, that consumer retries against an erroring server
    # for the rest of the process's life, with no timeout of its own -- bounding
    # flush() alone isn't enough. Cached for the process: every call site (this
    # one, GovernedExecutor's per-tool-call tracing, conftest.py's test-run trace)
    # shares one cheap check instead of each independently risking the same hang.
    if not _health_checked:
        _health_checked = True
        try:
            import httpx

            resp = httpx.get(f"{settings.langfuse_host}/api/public/health", timeout=1.5)
            _health_ok = resp.status_code == 200
        except Exception:
            _health_ok = False
    if not _health_ok:
        return None
    # lazy import — offline use never needs the package
    from langfuse import Langfuse  # type: ignore[reportMissingTypeStubs]

    _configured_client = Langfuse(
        public_key=public_key,
        secret_key=secret_key,
        host=settings.langfuse_host,
        mask=mask_madras_data,
        environment=os.environ.get("MADRAS_ENV", "dev"),
    )
    return _configured_client


def start_turn_trace(*, session_id: str, agent_name: str, rank: str | None = None) -> str | None:
    """Start (or resume) the one trace for a Shadow session. Returns the trace_id
    to thread through `LLMRequest.metadata["langfuse_trace_id"]` (nests LLM
    generations) and `GovernedExecutor.execute(...)` (nests tool spans).

    Uses ``id=session_id`` deterministically: `run_agentic_loop` is resumable
    (HITL pause/resume re-enters it), and calling `.trace(id=<same id>, ...)`
    again updates the existing trace instead of creating a new one — one trace
    per session survives every resume, not one trace per invocation.

    Returns None if Langfuse isn't configured — callers must treat that as
    "skip tracing", never raise.
    """
    client = get_live_client()
    if client is None:
        return None
    try:
        tags = [agent_name] + ([rank] if rank else [])
        trace = client.trace(
            id=session_id, name=f"turn:{agent_name}", session_id=session_id, tags=tags
        )
        return trace.id
    except Exception:
        return None


def push_tool_span(
    *,
    trace_id: str | None,
    tool_name: str,
    args: dict[str, Any],
    ok: bool,
    denied: bool,
    result_summary: Any,
    latency_ms: float,
) -> None:
    """Nest a `tool.{name}` span under the turn's trace. Never raises — tracing
    must not be able to break a tool call (degrade-gracefully, matching the
    audit writer's own doctrine); no-op if tracing is off."""
    client = get_live_client()
    if client is None or trace_id is None:
        return
    try:
        client.span(
            trace_id=trace_id,
            name=f"tool.{tool_name}",
            input=args,  # masked by the client-level `mask=` hook before export
            output=result_summary,
            metadata={"ok": ok, "denied": denied},
            level="ERROR" if (not ok and not denied) else "DEFAULT",
        )
    except Exception:
        pass


def push_event(
    *,
    trace_id: str | None,
    name: str,
    input: Any = None,
    output: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Nest a point-in-time event (no duration — e.g. a memory write, a compaction)
    under the turn's trace. Never raises; no-op if tracing is off."""
    client = get_live_client()
    if client is None or trace_id is None:
        return
    try:
        client.event(
            trace_id=trace_id,
            name=name,
            input=input,
            output=output,
            metadata=metadata or {},
        )
    except Exception:
        pass


def push_action_scores(*, trace_id: str | None, signals: dict[str, Any]) -> None:
    """Push the 8 required per-action dims (`eval_/emitter.py`'s contract) as
    Langfuse scores tied to the turn's trace — ties debugging traces directly
    to the same signals the rank/eval system already computes. No-op if tracing
    is off. Non-numeric dims (trajectory_trace, tool_calls, tool_selection) are
    skipped — scores are numeric/categorical, not full payloads (those already
    live on the span itself via push_tool_span).
    """
    client = get_live_client()
    if client is None or trace_id is None:
        return
    numeric_dims = (
        "task_completion",
        "argument_correctness",
        "confidence",
        "latency_ms",
        "cost_usd",
        # Retry/recovery — GovernedExecutor._record already computes these
        # (attempts beyond 1 = an error was hit; ok+errors = recovered) but
        # they were never pushed as scores until now.
        "errors_encountered",
        "errors_recovered",
        "attempts",
    )
    for dim in numeric_dims:
        if dim not in signals:
            continue
        value = signals[dim]
        try:
            client.score(
                trace_id=trace_id,
                name=dim,
                value=float(value) if isinstance(value, bool) else value,
            )
        except Exception:
            pass


def start_test_run_trace(*, run_id: str, source: str) -> str | None:
    """Start one trace for a whole pytest invocation (``tests/conftest.py``'s
    ``pytest_configure`` hook). ``source`` distinguishes which coding tool triggered
    the run — set via ``MADRAS_TRACE_SOURCE`` (e.g. ``claude-code``/``opencode``/
    ``codex``); feeds the Data Engine's Trajectory Lake (D41) the same way real
    agent turns do — "save the entire trace, not just the answer."

    Returns None if Langfuse isn't configured — callers must treat that as
    "skip tracing", never raise.
    """
    client = get_live_client()
    if client is None:
        return None
    try:
        trace = client.trace(
            id=run_id,
            name=f"pytest:{source}",
            session_id=run_id,
            tags=["pytest", source],
        )
        return trace.id
    except Exception:
        return None


PRODUCER_LANGFUSE_TRACES = "langfuse-traces"


def fetch_traces_for_training(
    *,
    tags: list[str] | None = None,
    from_ts: str | None = None,
    to_ts: str | None = None,
) -> list[dict[str, Any]]:
    """Query real production traces for training-data mining -- the first READ path on
    this client (every other function here is write/push-only). Every input/output was
    masked by `mask_madras_data` before it was ever written to Langfuse (D-ASI02) -- no
    additional redaction is needed here on read.

    Real finding (live-verified against madras-langfuse): `client.api.trace.list(...)`
    does NOT populate nested observations on its results (`trace.observations` is None) --
    only `client.api.trace.get(trace_id)` returns them. list() is used only to page
    through matching trace ids (filtered by tag/date range); get() is then called per id
    to fetch the full trace with observations, since shape_traces_to_sft_rows needs them.

    Unlike this module's tracing calls, a failed fetch RAISES rather than degrading to
    an empty list: this feeds a training run someone explicitly triggered, and silently
    returning zero rows would produce a misleadingly small/empty dataset with no visible
    cause."""
    client = get_live_client()
    if client is None:
        raise RuntimeError("Langfuse is not configured -- cannot fetch traces for training")
    summaries: list[Any] = client.api.trace.list(
        tags=tags, from_timestamp=from_ts, to_timestamp=to_ts
    ).data
    full_traces: list[dict[str, Any]] = []
    for summary in summaries:
        trace_id = cast("str", summary["id"] if isinstance(summary, dict) else summary.id)
        full = client.api.trace.get(trace_id)
        full_dict = cast("dict[str, Any]", full if isinstance(full, dict) else dict(full))
        # Real bug (live-verified): dict(full) only shallow-converts the trace itself --
        # `observations` is still a list of `ObservationsView` SDK objects, not plain
        # dicts, so shape_traces_to_sft_rows's `.get("type")` calls raised AttributeError.
        # ObservationsView supports dict() conversion (pydantic-style __iter__) same as
        # the trace object itself.
        raw_observations: list[Any] = full_dict.get("observations") or []
        full_dict["observations"] = [
            obs if isinstance(obs, dict) else cast("dict[str, Any]", dict(obs))
            for obs in raw_observations
        ]
        full_traces.append(full_dict)
    return full_traces


def _observation_role(observation: dict[str, Any]) -> str | None:
    """Best-effort role classification for a Langfuse observation: generations carry an
    explicit `input`/`output` pair (LLM call) treated as one user/assistant turn; other
    observation types (tool spans, events) aren't conversational turns and are skipped."""
    return "generation" if observation.get("type") == "GENERATION" else None


def shape_traces_to_sft_rows(
    traces: list[dict[str, Any]],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Pure shaping function (no Langfuse call) so this is unit-testable without a live
    instance: `fetch_traces_for_training` (network) and this (shaping) are split exactly
    like `SyntheticDataKitBridge.run_pipeline` (network) vs. `parse_chatml_export` (pure)
    are already split in dataset_compiler.py.

    Full multi-turn expansion (per design spec): every generation observation with both
    a non-empty input and output becomes one row, not just the trace's root input/output
    -- a 4-turn trace yields up to 4 rows. Observations with no assistant output (e.g. an
    in-flight or errored generation) are skipped."""
    rows: list[dict[str, Any]] = []
    for trace in traces:
        trace_id = trace.get("id")
        observations: list[dict[str, Any]] = trace.get("observations") or []
        for obs in observations:
            if _observation_role(obs) != "generation":
                continue
            prompt: Any = obs.get("input")
            completion: Any = obs.get("output")
            if not prompt or not completion:
                continue
            prompt_text = prompt if isinstance(prompt, str) else json.dumps(prompt)
            completion_text = completion if isinstance(completion, str) else json.dumps(completion)
            row_key = hashlib.sha256(
                f"{PRODUCER_LANGFUSE_TRACES}|{mining_run_id}|{obs.get('id')}".encode()
            ).hexdigest()[:16]
            rows.append(
                {
                    "id": f"sft-{row_key}",
                    "tenant": tenant,
                    "consent": consent,
                    "producer": PRODUCER_LANGFUSE_TRACES,
                    "source_id": trace_id,
                    "prompt": prompt_text,
                    "completion": completion_text,
                    "score": None,
                    "provenance": {
                        "mining_run_id": mining_run_id,
                        "producer": PRODUCER_LANGFUSE_TRACES,
                        "observation_id": obs.get("id"),
                    },
                }
            )
    return rows


def push_test_result(
    *,
    trace_id: str | None,
    test_name: str,
    outcome: str,
    duration_s: float,
    error: str | None = None,
) -> None:
    """Nest one test's result as a span under the run's trace. ``outcome`` is
    pytest's own ``passed``/``failed``/``skipped``. Never raises; no-op if tracing
    is off."""
    client = get_live_client()
    if client is None or trace_id is None:
        return
    try:
        client.span(
            trace_id=trace_id,
            name=test_name,
            output=error,  # masked by the client-level `mask=` hook before export
            metadata={"outcome": outcome, "duration_s": duration_s},
            level="ERROR" if outcome == "failed" else "DEFAULT",
        )
    except Exception:
        pass
