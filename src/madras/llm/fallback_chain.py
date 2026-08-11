"""Free-fleet fallback-chain on error (row 94).

B35 routing degrades on *availability* (pre-call); this adds the on-ERROR chain OpenRouter's
`models:[...]` / `route:fallback` gives: when a model call fails with a transient/capacity error
(429 / 5xx / timeout / moderation / downtime), move to the NEXT model in the chain.

Two hard rules baked in (the zero-cost moat + no-API-hammering):
- **one attempt per model** — never retry the same model (that is the retry-storm we must avoid);
  a fallback moves ON, it does not hammer.
- **terminal errors short-circuit** — a non-transient failure (bad request / auth / unknown) stops
  the chain immediately (it would fail everywhere), so we never burn the whole free fleet on a bug.

The chain is the free fleet (e.g. `task_router`'s candidate list). Pure + injectable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# transient/capacity signals that justify trying the next model
_FALLBACK_SIGNALS = (
    "429",
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "capacity",
    "timeout",
    "timed out",
    "502",
    "503",
    "504",
    "server error",
    "unavailable",
    "downtime",
    "moderation",
    "content_filter",
    "content filter",
    "flagged",
)


def classify_error(exc: BaseException) -> str:
    """'fallback' for a recognized transient/capacity error, else 'terminal' (conservative:
    unknown errors do NOT fall through the whole chain)."""
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status in (429, 502, 503, 504):
        return "fallback"
    blob = f"{type(exc).__name__} {exc}".lower()
    if any(sig in blob for sig in _FALLBACK_SIGNALS):
        return "fallback"
    return "terminal"


@dataclass
class Attempt:
    model: str
    error: str


@dataclass
class FallbackResult:
    ok: bool
    model: str = ""
    value: Any = None
    attempts: list[Attempt] = field(default_factory=list[Attempt])
    reason: str = ""


def run_with_fallback(
    chain: list[str],
    call: Callable[[str], Any],
    *,
    classify: Callable[[BaseException], str] = classify_error,
) -> FallbackResult:
    """Try each model in `chain` ONCE, in order. `call(model) -> value` runs the request.

    On success: return immediately. On a fallback-worthy error: record it and move to the next
    model. On a terminal error: stop (it would fail everywhere). Chain exhausted -> ok=False.
    """
    result = FallbackResult(ok=False)
    if not chain:
        result.reason = "empty fallback chain"
        return result
    for model in chain:
        try:
            result.value = call(model)
            result.ok = True
            result.model = model
            return result
        except BaseException as exc:  # classify decides fallback vs terminal
            kind = classify(exc)
            result.attempts.append(Attempt(model, f"{type(exc).__name__}: {exc}"))
            if kind == "terminal":
                result.reason = f"terminal error on {model} (no fallback): {exc}"
                return result
    result.reason = f"all {len(chain)} models in the fallback chain failed (transient)"
    return result


async def run_with_fallback_async(
    chain: list[str],
    call: Callable[[str], Any],
    *,
    classify: Callable[[BaseException], str] = classify_error,
) -> FallbackResult:
    """Async sibling of `run_with_fallback` -- `call(model)` is awaited, same one-attempt-
    per-model / terminal-short-circuits contract. Needed because `LLMGateway.complete()` is
    async (s46: the sync version can't wrap it without blocking the event loop)."""
    result = FallbackResult(ok=False)
    if not chain:
        result.reason = "empty fallback chain"
        return result
    for model in chain:
        try:
            result.value = await call(model)
            result.ok = True
            result.model = model
            return result
        except BaseException as exc:  # classify decides fallback vs terminal
            kind = classify(exc)
            result.attempts.append(Attempt(model, f"{type(exc).__name__}: {exc}"))
            if kind == "terminal":
                result.reason = f"terminal error on {model} (no fallback): {exc}"
                return result
    result.reason = f"all {len(chain)} models in the fallback chain failed (transient)"
    return result
