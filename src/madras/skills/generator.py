"""Skill auto-generation — propose a reusable skill from a completed session.

Trigger (Hermes pattern): a task worth distilling = 5+ tool calls OR error-recovery
OR a user correction. The synthesizer asks the model to distill the REUSABLE
procedure (general, not instance-specific). The result is a CANDIDATE only —
validation (M2F-T4) and human approval (M2F-T5) gate adoption.
"""

from __future__ import annotations

from dataclasses import dataclass

from madras.llm.gateway import LLMGateway, LLMRequest
from madras.skills.format import Skill, parse_skill_md

_MIN_TOOL_CALLS = 5


@dataclass
class GenerationSignals:
    tool_call_count: int = 0
    had_error_recovery: bool = False  # a tool failed then a retry/alt succeeded
    had_correction: bool = False  # the user corrected the agent mid-task


def should_propose_skill(sig: GenerationSignals) -> bool:
    return sig.tool_call_count >= _MIN_TOOL_CALLS or sig.had_error_recovery or sig.had_correction


async def synthesize_skill(
    *,
    gateway: LLMGateway,
    model: str,
    task: str,
    transcript: str,
    used_toolsets: list[str],
) -> Skill | None:
    """Ask the model to distill a reusable SKILL.md from the session. Returns a
    candidate Skill (toolsets scoped to what the procedure actually used), or None."""
    prompt = (
        "You just completed a task. Distill the REUSABLE procedure into a skill that "
        "would help with SIMILAR future tasks — general, not specific to this instance "
        "(no one-off names, dates, or values). Output ONLY a SKILL.md: YAML frontmatter "
        "with `name` (kebab-case) and `description` (one line), then markdown steps.\n\n"
        f"Task: {task}\n\n<retrieved>\n{transcript[:4000]}\n</retrieved>"
    )
    resp = await gateway.complete(
        LLMRequest(model=model, messages=[{"role": "user", "content": prompt}])
    )
    skill = parse_skill_md(resp.text)
    if not skill.name or not skill.description:
        return None
    # Governance: a skill may only use toolsets it actually exercised.
    skill.toolsets = sorted(set(used_toolsets))
    return skill
