"""Capability routing — `require_parameters` (row 96).

B35 routes by name; this gates routing on what the task actually REQUIRES (OpenRouter's
`require_parameters`): if the call needs tool-calling / structured output / long context / an image
modality, route ONLY to models that support it — a non-capable model would just error. The hard gate
that runs BEFORE the row-95 preference policy: `route_capable` (gate) -> `apply_policy` (prefs) ->
`policy_chain` -> `run_with_fallback`. Over the row-93 Model Catalog. Pure stdlib.
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.llm.model_catalog import ModelInfo


@dataclass
class CapabilityReq:
    tools: bool = False
    structured: bool = False
    reasoning: bool = False
    min_context: int = 0
    modality: str = ""  # required input modality, e.g. "image"


def exclusion_reasons(req: CapabilityReq, model: ModelInfo) -> list[str]:
    """Why `model` fails to satisfy `req` (empty list => it is capable)."""
    reasons: list[str] = []
    if req.tools and not model.tool_call:
        reasons.append("no tool_call")
    if req.structured and not model.structured_output:
        reasons.append("no structured_output")
    if req.reasoning and not model.reasoning:
        reasons.append("no reasoning")
    if req.min_context and model.context_window < req.min_context:
        reasons.append(f"context {model.context_window} < {req.min_context}")
    if req.modality and req.modality not in model.input_modalities:
        reasons.append(f"no {req.modality} input")
    return reasons


def supports(model: ModelInfo, req: CapabilityReq) -> bool:
    return not exclusion_reasons(req, model)


def route_capable(req: CapabilityReq, candidates: list[ModelInfo]) -> list[ModelInfo]:
    """Keep only the models that satisfy every required capability."""
    return [m for m in candidates if supports(m, req)]
