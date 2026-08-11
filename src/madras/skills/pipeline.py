"""End-to-end self-improvement step: trigger -> synthesize -> validate -> store candidate.

A candidate is only stored (awaiting HUMAN approval) if it passes the validation gates.
Best-effort: never raises into the turn.
"""

from __future__ import annotations

from typing import Any

from madras.llm.gateway import LLMGateway
from madras.skills.generator import GenerationSignals, should_propose_skill, synthesize_skill
from madras.skills.validation import validate_skill_candidate


async def maybe_generate_skill(
    *,
    signals: GenerationSignals,
    task: str,
    transcript: str,
    used_toolsets: list[str],
    gateway: LLMGateway,
    model: str,
    store: Any,
    project: str = "default",
    guard: Any = None,
    episodic: Any = None,
    stamped_at: str = "",
    source_session: str = "",
) -> str | None:
    """Returns the candidate skill name if one was proposed + validated + stored, else None."""
    if store is None or not should_propose_skill(signals):
        return None
    try:
        skill = await synthesize_skill(
            gateway=gateway,
            model=model,
            task=task,
            transcript=transcript,
            used_toolsets=used_toolsets,
        )
        if skill is None:
            return None
        result = await validate_skill_candidate(
            skill,
            gateway=gateway,
            model=model,
            guard=guard,
            episodic=episodic,
            stamped_at=stamped_at,
            source_session=source_session,
        )
        if not result.passed:
            return None
        await store.add_candidate(skill, project=project, provenance=result.provenance)
        return skill.name
    except Exception:
        return None
