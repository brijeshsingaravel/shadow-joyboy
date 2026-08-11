"""Judgment Engine (Human-Aligned frame §6.3, row judgment-engine) -- point the
eval_/proving_ground judge-panel machinery INWARD at the agent's own in-task decisions,
not just benchmark outputs.

`eval_/proving_ground/judge_panel.py` + `judge_runner.py` are a real multi-judge apparatus,
but they judge the BENCHMARK's outputs after the fact. This is the self-judgment half: a
single, cheap judge call that bias-checks a SPECIFIC in-task decision (confirmation / sunk-
cost / recency / halo) at the moment it matters -- when the agent is about to retry or
continue down a path SelfMonitor has already flagged as an impasse (row self-monitoring).
Deliberately ONE model, not the full 5-judge panel: this fires inline mid-turn, so panel-
scale cost/latency would defeat the point. Fail-closed (never blocks the agent, only nudges)
-- a parse/gateway failure returns biased=False, same fail-closed contract as judge_runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, cast

from madras.llm.decode import repair_tool_args
from madras.llm.gateway import LLMGateway, LLMRequest

_RUBRIC = (
    "You are a rigorous decision-bias auditor. The agent is about to retry or continue a "
    "course of action after evidence it may not be working. Judge whether ONE of these "
    "biases explains the decision better than the evidence does:\n"
    "CONFIRMATION -- ignoring/downplaying evidence the approach isn't working.\n"
    "SUNK_COST -- continuing because of prior investment (time/steps already spent), not "
    "because success is actually likely.\n"
    "RECENCY -- over-weighting the single most recent result over the full pattern.\n"
    "HALO -- assuming an approach that worked elsewhere must work here too, without evidence.\n"
    'Reply with STRICT JSON: {"biased": true|false, "bias_kind": "confirmation|sunk_cost|'
    'recency|halo|none", "reason": "...", "recommendation": "..."} and nothing else.'
)

_FAIL_CLOSED_KIND = "unparseable"


@dataclass
class JudgmentVerdict:
    biased: bool
    bias_kind: str = ""
    reason: str = ""
    recommendation: str = ""


def _parse(text: str) -> dict[str, Any]:
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        result = repair_tool_args(text)
        parsed = result.args if result.ok else None
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


async def judge_decision(
    *,
    gateway: LLMGateway,
    model: str,
    decision: str,
    evidence: str,
) -> JudgmentVerdict:
    """Bias-check ONE in-task decision. `decision` is what the agent is about to do;
    `evidence` is the recent outcome history that decision is (or isn't) grounded in."""
    req = LLMRequest(
        model=model,
        messages=[
            {"role": "system", "content": _RUBRIC},
            {"role": "user", "content": f"DECISION: {decision}\n\nEVIDENCE:\n{evidence}"},
        ],
        max_tokens=300,
        temperature=0.0,
    )
    try:
        resp = await gateway.complete(req)
    except Exception:
        return JudgmentVerdict(biased=False, bias_kind=_FAIL_CLOSED_KIND)

    parsed = _parse(resp.text)
    if not parsed:
        return JudgmentVerdict(biased=False, bias_kind=_FAIL_CLOSED_KIND)
    return JudgmentVerdict(
        biased=bool(parsed.get("biased", False)),
        bias_kind=str(parsed.get("bias_kind", "")),
        reason=str(parsed.get("reason", "")),
        recommendation=str(parsed.get("recommendation", "")),
    )
