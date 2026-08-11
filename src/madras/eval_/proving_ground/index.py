"""Madras Index — the artificialanalysis-style composite, weighted per tier.

The same suite scores compose differently per commercial tier:
  * free    — sells CAPABILITY lift (cheap model made good) -> weight raw-capability suites.
  * premium — sells CONTROL lift (governance/memory/agents) -> weight madras_features.
  * byok    — balanced.
Unweighted suites get a small default weight so any present suite counts.
"""

from __future__ import annotations

# suite_id -> weight, per tier. Missing suites fall back to _DEFAULT_W.
_CAPABILITY = {
    "bfcl": 2.0,
    "tau2": 2.0,
    "gpqa": 1.5,
    "gsm8k": 1.0,
    "mmlu_pro": 1.0,
    "swebench": 2.0,
    "terminal_bench": 1.5,
    "agentbench": 1.5,
    "gaia": 1.5,
    "madras_features": 1.0,
    "longmemeval": 1.0,
    # s24 outlier dims (capability/web/GUI/knowledge):
    "frames": 1.5,
    "sealqa": 1.5,
    "screenspot": 1.5,
    "memoryagentbench": 1.0,
    "conflictqa": 1.0,
    "knowedit": 1.0,
    "epmemory": 1.0,
    "questclarify": 1.0,
}
_CONTROL = {
    "madras_features": 3.0,
    "longmemeval": 2.0,
    "agentharm": 1.5,
    "tau2": 1.5,
    "bfcl": 1.0,
    "gpqa": 0.5,
    "gsm8k": 0.5,
    "mmlu_pro": 0.5,
    "swebench": 1.0,
    # s24 outlier dims (the control/moat — governance/memory/clarify/persona):
    "compounding": 3.0,
    "mcppoison": 2.0,
    "agentsafety": 2.0,
    "injecagent": 2.0,
    "memoryagentbench": 2.0,
    "abgcoqa": 1.5,
    "questclarify": 1.5,
    "epmemory": 1.5,
    "conflictqa": 1.5,
    "rolebench": 1.5,
    "personagym": 1.5,
    "knowedit": 1.0,
}
_BALANCED = {
    "madras_features": 1.5,
    "tau2": 1.5,
    "bfcl": 1.5,
    "longmemeval": 1.0,
    "compounding": 1.5,
    "memoryagentbench": 1.0,
    "agentsafety": 1.0,
    "frames": 1.0,
}

TIER_WEIGHTS: dict[str, dict[str, float]] = {
    "free": _CAPABILITY,
    "premium": _CONTROL,
    "byok": _BALANCED,
}
_DEFAULT_W = 0.5


def madras_index(per_suite: dict[str, float], tier: str = "free") -> float:
    """Weighted mean of present suite scores for the tier (reweighted on missing)."""
    weights = TIER_WEIGHTS.get(tier, _CAPABILITY)
    num = den = 0.0
    for suite, score in per_suite.items():
        w = weights.get(suite, _DEFAULT_W)
        num += w * max(0.0, min(1.0, float(score)))
        den += w
    return round(num / den, 4) if den else 0.0


def scaffold_lift(agent_index: float, raw_index: float) -> float:
    """How much the Shadow scaffold lifts the bare model on the same suites."""
    return round(float(agent_index) - float(raw_index), 4)
