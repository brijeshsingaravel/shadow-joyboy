"""skills/train.py — § C1 Block Workshop: reflectively TRAIN a skill (a "block") with the
real GEPA optimizer (optimizer/evolve.py). The optimizer already accepts a skill as a
Target (TARGET_KINDS includes "skill"); this wires it to a live skill: rewrite the body
to be clearer/more reusable, score each candidate against the skill's stated purpose, and
return a gated proposal. propose-not-dispose — the improved body is stored as a *candidate*
(needs the same human approval as any other version), never applied silently.

The evaluate/reflect functions are model calls, so a real train run needs the LLM (unlike
inspect/modify/version/curate, which are pure store ops). Injectable gateway keeps it
testable without a network call.
"""

from __future__ import annotations

from madras.llm.gateway import LLMGateway, LLMRequest
from madras.optimizer.evolve import evolve
from madras.optimizer.models import OptimProposal, Target
from madras.skills.format import Skill
from madras.skills.store import SkillStore

_SCORE_PROMPT = (
    "Score this skill procedure from 0.0 to 1.0 on how clearly and reusably it "
    "accomplishes its stated purpose. Reply with ONLY the number.\n\n"
    "Purpose: {desc}\n\nProcedure:\n{body}"
)
_REWRITE_PROMPT = (
    "This is a reusable skill procedure. Rewrite it to be clearer, more reusable, and "
    "more reliable at its stated purpose — same intent, better execution. Reply with "
    "ONLY the rewritten procedure, no preamble.\n\n"
    "Purpose: {desc}\n\nCurrent procedure:\n{body}"
)


def _parse_score(text: str) -> float:
    import re

    m = re.search(r"(\d+(?:\.\d+)?)", text or "")
    if not m:
        return 0.0
    try:
        return max(0.0, min(1.0, float(m.group(1))))
    except ValueError:
        return 0.0


async def train_skill(
    *,
    store: SkillStore,
    gateway: LLMGateway,
    model: str,
    project: str,
    name: str,
    rounds: int = 1,
) -> OptimProposal | None:
    """Run the GEPA loop on an ACTIVE skill's body. Returns the gated proposal (with the
    measured lift) and, if it improved, stores the new body as a candidate version.
    None if the skill doesn't exist."""
    skill = await store.get(name, project=project)
    if skill is None:
        return None

    async def _reflect(text: str, _failures: list[str]) -> str:
        req = LLMRequest(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": _REWRITE_PROMPT.format(desc=skill.description, body=text),
                }
            ],
        )
        resp = await gateway.complete(req)
        return resp.text.strip()

    async def _evaluate(text: str) -> dict[str, float]:
        req = LLMRequest(
            model=model,
            messages=[
                {"role": "user", "content": _SCORE_PROMPT.format(desc=skill.description, body=text)}
            ],
        )
        resp = await gateway.complete(req)
        return {"quality": _parse_score(resp.text)}

    target = Target(kind="skill", id=name, current_text=skill.body)
    proposal = await evolve(target, evaluate=_evaluate, reflect=_reflect, rounds=rounds)

    if proposal.improved:
        # propose-not-dispose: the trained body enters the SAME candidate → approve gate.
        await store.add_candidate(
            Skill(
                name=name,
                description=skill.description,
                body=proposal.new_text,
                toolsets=skill.toolsets,
                category=skill.category,
            ),
            project=project,
            provenance={
                "trained": True,
                "lift": round(proposal.lift, 4),
                "rounds": proposal.rounds,
            },
        )
    return proposal
