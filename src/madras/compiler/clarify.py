"""compiler/clarify.py — EIG-based ambiguity assessment (E1 Task B3, design corrected s38).

One structured LLM call self-reports an Expected-Information-Gain-style uncertainty
score for a compiled AgentSpec + its original outcome text, per the current research
(June 2026: "Uncertainty-Aware Clarification in LLM Agents with Information Gain",
"Uncertainty Decomposition for Clarification Seeking") -- not multi-sample estimation.
This is the Compiler's mechanism specifically; the live agentic loop's cheap,
zero-cost clarify/ambiguity.py heuristic is untouched (promoting this method there is
a separate, tracked initiative -- see Knowledge/Ideas.md).
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.llm.gateway import LLMGateway
from madras.llm.structured import structured_output
from madras.models.agent_spec import AgentSpec

_SCHEMA = {
    "type": "object",
    "required": ["uncertainty_score"],
    "properties": {
        "uncertainty_score": {"type": "number"},
        "candidate_question": {"type": "string"},
        "assumption_if_proceeding": {"type": "string"},
    },
}


class ClarityAssessmentError(Exception):
    """The model failed to produce a valid uncertainty assessment after retries."""


@dataclass
class ClarityAssessment:
    action: str  # "ask" | "proceed"
    uncertainty_score: float
    question: str = ""
    options: list[str] | None = None
    assumption: str = ""


def _prompt(spec: AgentSpec) -> str:
    return (
        "You are about to compile a governed agent from this specification. Assess how "
        "much residual uncertainty remains about what the user actually wants.\n\n"
        f'Original outcome: "{spec.outcome}"\n'
        f"Compiled name: {spec.name}\n"
        f"Persona: {spec.persona_voice}\n"
        f"Capabilities selected: {spec.capabilities}\n\n"
        "Return uncertainty_score (0.0 = fully clear, 1.0 = totally ambiguous), and EITHER "
        "a candidate_question that would most reduce that uncertainty (if score is high) "
        "OR an assumption_if_proceeding stating what you'd assume (if score is low)."
    )


async def needs_clarification(
    spec: AgentSpec,
    gateway: LLMGateway,
    model: str,
    *,
    threshold: float = 0.5,
) -> ClarityAssessment:
    result = await structured_output(
        gateway,
        model,
        [{"role": "user", "content": _prompt(spec)}],
        _SCHEMA,
        max_retries=2,
    )
    if not result.ok:
        raise ClarityAssessmentError(result.error)

    score = result.data.get("uncertainty_score")
    if not isinstance(score, (int, float)):
        raise ClarityAssessmentError("response missing a numeric uncertainty_score")
    score = float(score)

    if score >= threshold:
        return ClarityAssessment(
            action="ask",
            uncertainty_score=score,
            question=result.data.get("candidate_question", ""),
            options=result.data.get("options"),
        )
    return ClarityAssessment(
        action="proceed",
        uncertainty_score=score,
        assumption=result.data.get("assumption_if_proceeding", ""),
    )
