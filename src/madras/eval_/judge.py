"""Agent-as-Judge dispatcher — escalates high-stakes eval decisions to an LLM judge.

Five named triggers require agent-level judgment; all other eval is handled
by the lightweight dimension scorers in dimensions.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from madras.llm.gateway import LLMGateway, LLMRequest


class JudgeTrigger(str, Enum):
    PROMOTION_GATE = "promotion_gate"
    DEMOTION_REVIEW = "demotion_review"
    RED_TEAM_RELEASE_GATE = "red_team_release_gate"
    ASI_AUDIT = "asi_audit"
    MARKETPLACE_LISTING = "marketplace_listing"


AGENT_JUDGE_TRIGGERS: frozenset[str] = frozenset(t.value for t in JudgeTrigger)

_JUDGE_SYSTEM_PROMPT = (
    "You are a rigorous agent quality judge. "
    "Given an evaluation context, respond with PASS or FAIL on the first line, "
    "followed by a clear rationale. Be concise and precise."
)


@dataclass
class JudgeVerdict:
    trigger: str
    verdict: str  # "PASS" | "FAIL"
    rationale: str


class JudgeDispatcher:
    """Routes eval decisions: routine signals stay in dimensions.py;
    high-stakes triggers escalate to an LLM judge via LLMGateway."""

    def needs_agent_judge(self, trigger: str | JudgeTrigger) -> bool:
        """Return True iff trigger is one of the 5 escalation triggers."""
        value = trigger.value if isinstance(trigger, JudgeTrigger) else trigger
        return value in AGENT_JUDGE_TRIGGERS

    async def dispatch(
        self,
        trigger: str | JudgeTrigger,
        context: str,
        *,
        gateway: LLMGateway,
        model: str = "anthropic/claude-sonnet-4-6",
    ) -> JudgeVerdict:
        """Call the LLM judge with a rubric over *context* and return a verdict.

        The first line of the response is parsed: starts with PASS (case-insensitive)
        → "PASS", anything else → "FAIL". The full response text is stored as rationale.
        """
        trigger_value = trigger.value if isinstance(trigger, JudgeTrigger) else trigger

        req = LLMRequest(
            model=model,
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (f"Trigger: {trigger_value}\n\nEvaluation context:\n{context}"),
                },
            ],
            max_tokens=512,
            temperature=0.0,
        )

        resp = await gateway.complete(req)
        first_line = resp.text.split("\n")[0].strip()
        verdict = "PASS" if first_line.upper().startswith("PASS") else "FAIL"

        return JudgeVerdict(
            trigger=trigger_value,
            verdict=verdict,
            rationale=resp.text,
        )
