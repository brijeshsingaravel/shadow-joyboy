"""Action-level self-critique (Reflexion at the action level) — P0 #4.

When an action fails, the agent critiques its own failed call and proposes corrected arguments,
then retries (bounded). This is distinct from the transient-failure retry in GovernedExecutor
(network blips) and from the pre-done verify reflex: this reasons about *why the action failed*
and fixes the call. Off by default; enabled per-agent via ExecutionConfig.self_critique.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast

from madras.llm.gateway import LLMGateway, LLMRequest


class _Result(Protocol):  # pyright: ignore[reportUnusedClass]
    """Documents the duck-typed shape `run_with_self_critique`'s `execute` result must expose
    (e.g. ToolResult). Not wired into the signature itself: real callers return richer objects
    (content/extras/...) that would fail a strict `_Result`-typed contract."""

    ok: bool
    error: str | None


@dataclass
class Critique:
    should_retry: bool
    corrected_args: dict[str, Any] | None
    reason: str


_PROMPT = """An agent's tool action just FAILED. Decide whether changing the ARGUMENTS would fix \
it.

Tool: {tool}
Arguments used: {args}
Error returned: {error}

If the error implies a different argument value would succeed (wrong format/extension, \
out-of-range, missing or misspelled field, bad type), set should_retry=true and give the FULL \
corrected arguments. Only set should_retry=false when no argument change could help (missing \
resource, wrong tool entirely, impossible request).

Reply with ONLY a JSON object. Example shape (not the answer):
{{"should_retry": true, "corrected_args": {{"limit": 10}}, "reason": "limit must be <= 10"}}"""


def _parse_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of the first JSON object from a model reply."""
    s = text.strip()
    if s.startswith("```"):
        s = s.strip("`")
        s = s[s.find("{") :] if "{" in s else s
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        out = json.loads(s[start : end + 1])
        return cast("dict[str, Any]", out) if isinstance(out, dict) else {}
    except json.JSONDecodeError:
        return {}


async def critique_action(
    gateway: LLMGateway, *, tool: str, args: dict[str, Any], error: str, model: str
) -> Critique:
    """Ask the model to critique a failed action and propose corrected arguments."""
    prompt = _PROMPT.format(tool=tool, args=json.dumps(args), error=error or "(no error text)")
    resp = await gateway.complete(
        LLMRequest(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=400,
            temperature=0.0,
        )
    )
    data = _parse_json(resp.text)
    corrected: Any = data.get("corrected_args")
    return Critique(
        should_retry=bool(data.get("should_retry")) and isinstance(corrected, dict),
        corrected_args=cast("dict[str, Any]", corrected) if isinstance(corrected, dict) else None,
        reason=str(data.get("reason", "")),
    )


async def run_with_self_critique(
    *,
    execute: Callable[[dict[str, Any]], Awaitable[Any]],
    tool: str,
    args: dict[str, Any],
    gateway: LLMGateway,
    model: str,
    max_retries: int = 1,
) -> tuple[Any, int, list[str]]:
    """Run ``execute(args)``; on failure, self-critique → correct → retry (up to max_retries).

    Returns (final_result, attempts, critique_reasons). ``execute`` returns any object exposing
    ``ok`` and ``error`` (e.g. ToolResult). Errors stay visible — a non-retryable critique stops.
    """
    result = await execute(args)
    attempts = 1
    reasons: list[str] = []
    while not result.ok and attempts <= max_retries:
        crit = await critique_action(
            gateway, tool=tool, args=args, error=getattr(result, "error", "") or "", model=model
        )
        reasons.append(crit.reason)
        if not crit.should_retry or crit.corrected_args is None:
            break
        args = crit.corrected_args
        result = await execute(args)
        attempts += 1
    return result, attempts, reasons
