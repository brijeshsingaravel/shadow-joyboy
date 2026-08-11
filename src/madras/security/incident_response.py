"""Defense Engine's incident-response dispatcher (row defense-engine).

The note's own conclusion: "the upgrade is L2->L5, built ON TOP OF existing
mechanisms (they become the respond step of a larger cycle) -- not a rebuild."
`workspace/killswitch.py::QuarantineStore.quarantine_agent` is already the real,
live "halt" action; this module adds the missing piece -- a deterministic dispatcher
that maps an incident CLASS to the right existing action. Classes are keyed off
Madras's own ASI-taxonomy `category` tags (`security/asi_redteam.py`), NOT a MITRE
ATLAS mapping (explicitly out of scope -- built separately, opencode). Scoped to the
classes with a real existing response primitive today; `memory_poisoning` (needs a
rollback-to-clean-checkpoint primitive, absent) and `model_failure` (needs a
safer-model-fallback primitive, absent) are left BUILD, not faked.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class IncidentClass(str, Enum):
    PROMPT_INJECTION = "prompt_injection"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    TOOL_COMPROMISE = "tool_compromise"
    MEMORY_POISONING = "memory_poisoning"
    MODEL_FAILURE = "model_failure"
    UNKNOWN = "unknown"


# Madras's own ASI-taxonomy tags (security/asi_redteam.py's own category comments) ->
# incident class. ASI09 (audit evasion) / ASI10 (rogue persistence) both read as an
# attempt to escalate beyond governance, so they bucket into privilege_escalation.
_ASI_CATEGORY_MAP: dict[str, IncidentClass] = {
    "ASI01": IncidentClass.PRIVILEGE_ESCALATION,  # goal manipulation
    "ASI02": IncidentClass.PROMPT_INJECTION,
    "ASI03": IncidentClass.PRIVILEGE_ESCALATION,
    "ASI04": IncidentClass.TOOL_COMPROMISE,  # untrusted MCP server
    "ASI05": IncidentClass.TOOL_COMPROMISE,  # unsafe code exec
    "ASI06": IncidentClass.MEMORY_POISONING,
    "ASI07": IncidentClass.PROMPT_INJECTION,  # inter-agent instruction injection
    "ASI08": IncidentClass.TOOL_COMPROMISE,  # resource exhaustion via tool loop
    "ASI09": IncidentClass.PRIVILEGE_ESCALATION,  # audit-log evasion
    "ASI10": IncidentClass.PRIVILEGE_ESCALATION,  # rogue autonomy/persistence
}

# Classes with a real, live existing response primitive today (killswitch.py).
# memory_poisoning/model_failure have none -- BUILD, not faked.
_RESPONDS_WITH_QUARANTINE = frozenset(
    {
        IncidentClass.PRIVILEGE_ESCALATION,
        IncidentClass.TOOL_COMPROMISE,
    }
)


def classify_incident(category: str) -> IncidentClass:
    """Map an existing ASI-category string (as already emitted by guardrails.py /
    asi_redteam.py) to an incident class. Unknown/blank input -> UNKNOWN (safe default,
    logged only, never silently escalated)."""
    return _ASI_CATEGORY_MAP.get((category or "").strip().upper(), IncidentClass.UNKNOWN)


class KillSwitch(Protocol):
    async def quarantine_agent(self, *, agent_name: str, reason: str, by_actor: str) -> None: ...


@dataclass
class IncidentResponse:
    incident_class: IncidentClass
    action_taken: str  # "quarantine" | "log_only" | "no_primitive_available"
    detail: str


async def respond(
    incident_class: IncidentClass,
    *,
    agent_name: str,
    reason: str,
    killswitch: KillSwitch | None = None,
    by_actor: str = "defense_engine",
) -> IncidentResponse:
    """Deterministic dispatch: incident class -> the matching EXISTING response
    primitive. Never invents a response a primitive doesn't back."""
    if incident_class in _RESPONDS_WITH_QUARANTINE:
        if killswitch is None:
            return IncidentResponse(
                incident_class,
                "no_primitive_available",
                "quarantine indicated but no killswitch was provided",
            )
        await killswitch.quarantine_agent(agent_name=agent_name, reason=reason, by_actor=by_actor)
        return IncidentResponse(incident_class, "quarantine", f"quarantined {agent_name}: {reason}")

    if incident_class == IncidentClass.PROMPT_INJECTION:
        # Matches the note's own Acts: log first, slow-verify -- the existing
        # guardrail/rail scanners already blocked the injected content itself.
        return IncidentResponse(
            incident_class, "log_only", "logged; content already blocked at the rail"
        )

    if incident_class in (IncidentClass.MEMORY_POISONING, IncidentClass.MODEL_FAILURE):
        return IncidentResponse(
            incident_class,
            "no_primitive_available",
            f"{incident_class.value} has no response primitive yet -- BUILD, not faked",
        )

    return IncidentResponse(IncidentClass.UNKNOWN, "log_only", "unclassified incident, logged")
