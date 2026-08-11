"""Raw-model baseline: the bare model, NO Madras scaffold (no tools, no memory, no
governed loop) — one gateway.complete per resample, scored with the SAME deterministic
checks. Its score is the floor against which scaffold_lift is measured. A raw model
cannot call tools, so tool_called scenarios fail for it by construction — and that gap
is exactly the value Shadow's scaffold adds.
"""

from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.scenario import Scenario
from madras.eval_.proving_ground.scoring import score_deterministic
from madras.llm.gateway import LLMGateway, LLMRequest

_RAW_SYSTEM = (
    "You are a helpful assistant. Answer the user's request directly and concisely. "
    "You have no tools."
)


async def run_raw_case(
    scenario: Scenario, *, gateway: LLMGateway, model: str, k: int = 1
) -> dict[str, Any]:
    """Run the bare model k times on the scenario task; return pass_rate + token totals."""
    passes = 0
    in_tok = out_tok = 0
    cost = 0.0
    for _ in range(max(1, k)):
        req = LLMRequest(
            model=model,
            messages=[
                {"role": "system", "content": _RAW_SYSTEM},
                {"role": "user", "content": scenario.task},
            ],
        )
        resp = await gateway.complete(req)
        in_tok += resp.input_tokens
        out_tok += resp.output_tokens
        cost += resp.cost_usd
        traj: dict[str, Any] = {"answer": resp.text or "", "tools": [], "refused": False}
        if score_deterministic(scenario, traj).passed:
            passes += 1
    kk = max(1, k)
    return {
        "pass_rate": round(passes / kk, 4),
        "passes": passes,
        "k": kk,
        "tools_used": [],
        "in_tok": in_tok,
        "out_tok": out_tok,
        "cost_usd": cost,
    }


def raw_index_for(per_suite_scores: dict[str, list[float]]) -> dict[str, float]:
    """Mean raw pass_rate per suite -> the raw per-suite scores for the Madras Index."""
    return {s: round(sum(v) / len(v), 4) if v else 0.0 for s, v in per_suite_scores.items()}
