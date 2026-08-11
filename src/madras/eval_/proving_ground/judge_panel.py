"""Task 5 — independent 5-judge supermajority panel (no debate), with
meta-adjudication on close splits.

Each judge scores the trajectory INDEPENDENTLY (gathered concurrently, with no
cross-talk / debate / order coupling), rubric-anchored, returning
`{"pass": bool, "score": float, "reason": str}`. Supermajority >= threshold of
the panel passing -> the panel passes; dissent is recorded in every vote. A
judge that raises is recorded as a fail vote so one flaky model can never crash
the panel. The agent's own model is excluded from JUDGES_DEFAULT.

Judging is POINTWISE over a SINGLE trajectory: there are no candidate options
presented to a judge, so there is no option-order / position-bias surface and
nothing to shuffle. (Position-bias mitigation via randomized option order would
apply only to a future PAIRWISE mode, which is not implemented.) The bias
controls that DO apply to pointwise scoring are wired here:

  * Verbosity bias — the judge prompt forbids rewarding length (`judge_runner.py`),
    and the panel records the answer length + a `length_warn` flag so a long
    answer is visible; the meta-judge is told to ignore verbosity.
  * Disagreement / borderline bias — when the panel vote is CLOSE (one vote from
    flipping the supermajority, i.e. `n_pass in {threshold-1, threshold}`), ONE
    meta-judge `meta_call` is shown the task + rubric + the panel's individual
    votes/reasons (the dissent) and adjudicates. Its verdict is authoritative
    ONLY for close splits; clear consensus is left untouched. Use a DIFFERENT
    model than the panel (`meta_judge`) so the tie-break stays uncorrelated.

The real panel `call` (Plan 3 wires it) builds a strict rubric-anchored pointwise
prompt and routes each judge through LLMGateway on its model. Here `call` and
`meta_call` are injected so the protocol is testable without network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# Five diverse, free judges — one per model family (Nemotron · Moonshot · GLM ·
# Google · Qwen) so judge errors stay uncorrelated. All verified live on the
# LiteLLM proxy 2026-06-17 and routed to no-daily-cap tiers (NVIDIA NIM x4 +
# Gemini Flash), NOT Groq, so the panel survives constant sweeps. gpt-oss-120b
# (Groq, token-capped) → gemini-flash (1500/day, 1M ctx); qwen judge is qwen3.5
# on NVIDIA (the Cerebras qwen3-32b route is configured but its key is currently
# invalid). The agent's model (llama-70b) is absent → no self-preference bias.
JUDGES_DEFAULT = ["nemotron-super-120b", "kimi-k2", "glm-5.1", "gemini-flash", "qwen3.5"]

# Answer-length (chars) above which a PASS is flagged for possible verbosity bias.
# Recorded as a signal; the meta-judge is also told to ignore length.
VERBOSITY_LEN_WARN = 4000

# Injected meta-judge call: (rubric, task, trajectory, votes) -> {"pass", "reason"}.
MetaCall = Callable[[str, str, dict[str, Any], list[dict[str, Any]]], Awaitable[dict[str, Any]]]


@dataclass
class PanelVerdict:
    passed: bool
    n_pass: int
    votes: list[dict[str, Any]]  # [{judge, pass, score, reason}]
    meta_used: bool = False
    meta_reason: str | None = None
    answer_len: int = 0
    length_warn: bool = False


async def judge_panel(
    rubric: str,
    task: str,
    trajectory: dict[str, Any],
    *,
    judges: list[str],
    call: Callable[..., Awaitable[dict[str, Any]]],
    threshold: int = 4,
    meta_call: MetaCall | None = None,
    meta_judge: str | None = None,  # recorded by caller; selects the meta model upstream
) -> PanelVerdict:
    async def one(name: str) -> dict[str, Any]:
        try:
            v = await call(name, rubric, task, trajectory)
        except Exception as exc:  # a flaky judge votes fail — never crash the panel
            v = {"pass": False, "score": 0.0, "reason": f"judge error: {exc}"}
        return {
            "judge": name,
            "pass": bool(v.get("pass")),
            "score": float(v.get("score", 0.0)),
            "reason": str(v.get("reason", "")),
        }

    votes = await asyncio.gather(*(one(n) for n in judges))  # INDEPENDENT — no debate
    votes = list(votes)
    n_pass = sum(int(v["pass"]) for v in votes)
    passed = n_pass >= threshold

    answer_len = len(str(trajectory.get("answer", "")))
    length_warn = answer_len > VERBOSITY_LEN_WARN

    # A CLOSE split is one vote from flipping the supermajority either way.
    is_split = n_pass in {threshold - 1, threshold}
    meta_used = False
    meta_reason: str | None = None
    if is_split and meta_call is not None:
        try:
            mv = await meta_call(rubric, task, trajectory, votes)
        except Exception:  # meta backend down → fall back to the panel tally
            mv = None
        if isinstance(mv, dict):
            meta_used = True
            passed = bool(mv.get("pass"))
            meta_reason = str(mv.get("reason", ""))

    return PanelVerdict(
        passed=passed,
        n_pass=n_pass,
        votes=votes,
        meta_used=meta_used,
        meta_reason=meta_reason,
        answer_len=answer_len,
        length_warn=length_warn,
    )
