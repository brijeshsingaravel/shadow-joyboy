"""compiler/intent.py — outcome -> AgentSpec (LLM, structured, tier-aware) (E1 Task B2).

Composes three already-built primitives (no reimplementation): A1's Catalog, A4's
plan_entitlement_policy, and llm/structured.py's structured_output(). The caller's
entitled palette is baked into the schema (an enum) AND the prompt, so an out-of-tier
capability is never even offered as an option -- and re-checked defensively after
parsing (a model can still hallucinate outside the enum).
"""

from __future__ import annotations

import json

from madras_capabilities.catalog import Catalog
from madras_capabilities.tiers import plan_entitlement_policy

from madras.factory.dynamic import AuthContext
from madras.llm.gateway import LLMGateway
from madras.llm.structured import structured_output
from madras.models.agent_spec import NEIGHBOURHOODS, AgentSpec


class IntentCompilationError(Exception):
    """The model failed to produce a valid AgentSpec after retries."""


class CapabilityNotEntitled(ValueError):
    """The compiled spec declares a capability outside the caller's plan entitlement."""


def _schema(entitled_capability_ids: list[str]) -> dict[str, object]:
    return {
        "type": "object",
        "required": [
            "outcome",
            "name",
            "archetype",
            "neighborhood",
            "persona_voice",
            "persona_refusal_style",
            "persona_north_star",
            "discovery_summary",
            "execution",
        ],
        "properties": {
            "outcome": {"type": "string"},
            "name": {"type": "string"},
            "archetype": {"type": "string"},
            "neighborhood": {"type": "string", "enum": sorted(NEIGHBOURHOODS)},
            "persona_voice": {"type": "string"},
            "persona_refusal_style": {"type": "string"},
            "persona_north_star": {"type": "string"},
            "discovery_summary": {"type": "string"},
            "capabilities": {
                "type": "array",
                "items": {"type": "string", "enum": sorted(entitled_capability_ids)},
            },
            "skills": {"type": "array"},
            # "schedule" deliberately omitted from typed properties: it's Optional[str]
            # (often JSON null) and the minimal validate_against_schema() has no nullable-
            # type support -- AgentSpec's own Pydantic field validates the real value.
            "execution": {"type": "string", "enum": ["react", "plan_execute", "reflexion"]},
        },
    }


def _prompt(outcome: str, entitled_capability_ids: list[str]) -> str:
    return (
        "You are compiling a governed agent from a user's outcome description.\n"
        f'Outcome: "{outcome}"\n\n'
        "Choose zero or more capabilities ONLY from this list (the user's plan does not "
        f"unlock anything else): {json.dumps(sorted(entitled_capability_ids))}\n\n"
        "Pick a snake_case name, an archetype slug, one of the 9 neighbourhoods, and a "
        "discovery_summary (when should THIS agent be invoked -- distinct from its "
        "personality).\n\n"
        "Then write the agent's PERSONA as one coherent character, not three disconnected "
        "traits (a layered persona card: voice, behavioral rules, guiding worldview) -- "
        "persona_voice (how it sounds), persona_refusal_style (how it declines a request "
        "outside what it should do, and what it offers instead), and persona_north_star "
        "(the guiding narrative/worldview behind it). All three must feel like the same "
        "person.\n\n"
        "Finally pick an execution pattern (react | plan_execute | reflexion)."
    )


async def compile_intent(
    *,
    outcome: str,
    gateway: LLMGateway,
    model: str,
    catalog: Catalog,
    auth: AuthContext,
) -> AgentSpec:
    entitled = plan_entitlement_policy(catalog)(auth)
    result = await structured_output(
        gateway,
        model,
        [{"role": "user", "content": _prompt(outcome, sorted(entitled))}],
        _schema(sorted(entitled)),
        max_retries=2,
    )
    if not result.ok:
        raise IntentCompilationError(result.error)

    spec = AgentSpec.model_validate(result.data, context={"catalog": catalog})

    not_entitled = [c for c in spec.capabilities if c not in entitled]
    if not_entitled:
        raise CapabilityNotEntitled(
            f"capabilities not entitled for plan {auth.plan!r}: {not_entitled}"
        )
    return spec
