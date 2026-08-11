"""Plan 3 Task 1 — the real rubric-anchored pointwise judge `call`.

`make_judge_call(gateway_for)` returns the async `call(name, rubric, task,
trajectory)` that `judge_panel` invokes per model. Each call builds a STRICT
rubric-anchored prompt (judge only against the rubric; do not reward verbosity),
routes it through `gateway_for(name)` (a per-model `LLMGateway` factory), and
parses the model's STRICT-JSON verdict robustly via `repair_tool_args`. Any
parse failure or gateway exception votes fail-closed
(`{"pass": False, "score": 0.0, "reason": "unparseable"}`) so one bad model can
never inflate a score or crash the panel.

Judging is POINTWISE over a single trajectory — there are no candidate options
to present, so there is no option-order / position-bias surface and nothing to
shuffle (randomized option order would apply only to a future pairwise mode).
The verbosity guard here is the prompt instruction below; `judge_panel` also
records the answer length + a `length_warn` flag.

`make_meta_judge_call(gateway_for)` returns the async `meta_call(rubric, task,
trajectory, votes)` that `judge_panel` invokes ONLY on a close split: it is
shown the panel's individual votes/reasons and adjudicates `{"pass", "reason"}`,
told explicitly to ignore verbosity/length.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, cast

from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest

_SYSTEM = (
    "You are a rigorous agent-quality judge. Judge ONLY against the rubric. "
    "Do NOT reward verbosity or length. Reply with STRICT JSON: "
    '{"pass": true|false, "score": 0.0-1.0, "reason": "..."} and nothing else.'
)

_FAIL_CLOSED = {"pass": False, "score": 0.0, "reason": "unparseable"}


def _render_trajectory(trajectory: dict[str, Any]) -> str:
    answer = str(trajectory.get("answer", ""))
    tools: list[Any] = trajectory.get("tools", []) or []
    tool_names = ", ".join(str(t) for t in tools) if tools else "(none)"
    refused = bool(trajectory.get("refused", False))
    return f"ANSWER:\n{answer}\n\nTOOLS USED: {tool_names}\nREFUSED: {refused}"


def _build_user_msg(rubric: str, task: str, trajectory: dict[str, Any]) -> str:
    return (
        f"RUBRIC:\n{rubric}\n\n"
        f"TASK:\n{task}\n\n"
        f"AGENT TRAJECTORY:\n{_render_trajectory(trajectory)}\n\n"
        "Return the STRICT JSON verdict now."
    )


def _coerce_verdict(parsed: dict[str, Any]) -> dict[str, Any]:
    try:
        score = float(parsed.get("score", 0.0))
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))
    return {
        "pass": bool(parsed.get("pass", False)),
        "score": score,
        "reason": str(parsed.get("reason", "")),
    }


def make_judge_call(
    gateway_for: Callable[[str], LLMGateway],
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def call(name: str, rubric: str, task: str, trajectory: dict[str, Any]) -> dict[str, Any]:
        req = LLMRequest(
            model=name,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": _build_user_msg(rubric, task, trajectory)},
            ],
            max_tokens=512,
            temperature=0.0,
        )
        try:
            resp = await gateway_for(name).complete(req)
        except Exception:
            return dict(_FAIL_CLOSED)
        text = resp.text
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict):
            result = repair_tool_args(text)
            parsed = result.args if result.ok else None
        if not isinstance(parsed, dict):
            return dict(_FAIL_CLOSED)
        return _coerce_verdict(cast("dict[str, Any]", parsed))

    return call


_META_SYSTEM = (
    "You are a meta-judge breaking a TIE on a borderline agent-quality verdict. "
    "The panel split closely. Judge ONLY against the rubric. Do NOT reward "
    "verbosity or length. Weigh the dissenting reasons below, then give a final "
    'call. Reply with STRICT JSON: {"pass": true|false, "reason": "..."} '
    "and nothing else."
)

_META_FAIL_CLOSED = {"pass": False, "reason": "meta unparseable"}


def _render_votes(votes: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for v in votes:
        verdict = "PASS" if v.get("pass") else "FAIL"
        judge = str(v.get("judge", ""))
        score = str(v.get("score", ""))
        reason = str(v.get("reason", ""))
        lines.append(f"- {judge}: {verdict} (score={score}) — {reason}")
    return "\n".join(lines)


def make_meta_judge_call(
    gateway_for: Callable[[str], LLMGateway],
    meta_judge: str,
) -> Callable[..., Awaitable[dict[str, Any]]]:
    async def meta_call(
        rubric: str, task: str, trajectory: dict[str, Any], votes: list[dict[str, Any]]
    ) -> dict[str, Any]:
        user = (
            f"RUBRIC:\n{rubric}\n\n"
            f"TASK:\n{task}\n\n"
            f"AGENT TRAJECTORY:\n{_render_trajectory(trajectory)}\n\n"
            f"PANEL VOTES (the dissent to adjudicate):\n{_render_votes(votes)}\n\n"
            "Return the STRICT JSON final adjudication now."
        )
        req = LLMRequest(
            model=meta_judge,
            messages=[
                {"role": "system", "content": _META_SYSTEM},
                {"role": "user", "content": user},
            ],
            max_tokens=512,
            temperature=0.0,
        )
        try:
            resp = await gateway_for(meta_judge).complete(req)
        except Exception:
            return dict(_META_FAIL_CLOSED)
        try:
            parsed = json.loads(resp.text)
        except (json.JSONDecodeError, ValueError):
            parsed = None
        if not isinstance(parsed, dict):
            result = repair_tool_args(resp.text)
            parsed = result.args if result.ok else None
        if not isinstance(parsed, dict):
            return dict(_META_FAIL_CLOSED)
        verdict = cast("dict[str, Any]", parsed)
        return {"pass": bool(verdict.get("pass", False)), "reason": str(verdict.get("reason", ""))}

    return meta_call
