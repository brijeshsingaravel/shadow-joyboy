"""Interpretation Engine (row interpretation-engine) -- the 6-layer comprehension ladder
+ bias-on-interpretation detection.

Confirmed the most greenfield Human-Aligned faculty: no OSS ladder/taxonomy exists for
LLM reading-depth or interpretation-bias specifically (s46 research). Real daylight
from Judgment Engine (`metacog/judgment.py::judge_decision`): that audits DECISION
biases (sunk-cost/confirmation/recency/halo) over a decision+evidence input; this
audits how TEXT was READ, over raw content -- different rubric, no overlap. Reuses
`compiler/clarify.py`'s cleaner `structured_output` idiom (one schema-validated LLM
call, self-reported result) rather than judgment.py's hand-rolled parse path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from madras.llm.gateway import LLMGateway
from madras.llm.structured import structured_output

# The note's own 6-layer ladder, literal -> integrative.
LAYERS = ("literal", "contextual", "critical", "structural", "generative", "integrative")

# The note's own named interpretation biases (distinct from Judgment Engine's
# decision biases).
BIAS_TYPES = (
    "literalism",
    "projection",
    "over_generalization",
    "under_generalization",
    "authority_bias",
    "recency_bias",
)

_SCHEMA = {
    "type": "object",
    "required": ["recommended_layer"],
    "properties": {
        "recommended_layer": {"type": "string"},
        "bias_flags": {"type": "array"},
        "reasoning": {"type": "string"},
    },
}


class InterpretationAssessmentError(Exception):
    """The model failed to produce a valid interpretation assessment after retries."""


@dataclass
class InterpretationAssessment:
    recommended_layer: str
    bias_flags: list[str] = field(default_factory=list[str])
    reasoning: str = ""


def _prompt(content: str, context: str) -> str:
    ctx_block = f'\n\nSurrounding context: "{context}"' if context.strip() else ""
    return (
        "Assess how deeply the following content needs to be interpreted, and whether "
        "a surface reading would misread it.\n\n"
        f'Content: "{content}"{ctx_block}\n\n'
        f"Return recommended_layer -- ONE of {', '.join(LAYERS)} (literal = read it at "
        "face value is enough; integrative = it only makes sense synthesized with other "
        "context/knowledge). Return bias_flags -- zero or more of "
        f"{', '.join(BIAS_TYPES)} that a reader risks falling into on THIS content "
        "specifically (e.g. literalism = missing intent/tone behind literal words; "
        "projection = reading your own assumptions into ambiguous phrasing; "
        "over_generalization = treating one instance as a universal rule). Return "
        "reasoning -- one sentence."
    )


async def assess_interpretation(
    content: str,
    gateway: LLMGateway,
    model: str,
    *,
    context: str = "",
) -> InterpretationAssessment:
    content = (content or "").strip()
    if not content:
        raise InterpretationAssessmentError("content is required")

    result = await structured_output(
        gateway,
        model,
        [{"role": "user", "content": _prompt(content, context)}],
        _SCHEMA,
        max_retries=2,
    )
    if not result.ok:
        raise InterpretationAssessmentError(result.error)

    layer = str(result.data.get("recommended_layer", "")).strip().lower()
    if layer not in LAYERS:
        layer = "literal"  # safe default: no unwarranted depth assumed

    raw_flags: list[Any] = result.data.get("bias_flags") or []
    flags = [str(f).strip().lower() for f in raw_flags if str(f).strip().lower() in BIAS_TYPES]

    return InterpretationAssessment(
        recommended_layer=layer,
        bias_flags=flags,
        reasoning=str(result.data.get("reasoning", "")),
    )
