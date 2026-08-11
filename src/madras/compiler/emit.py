"""compiler/emit.py — AgentSpec -> role-layer dict (E1 Task B4).

Produces ONLY the role layer: identity + persona + capabilities + execution pattern.
Governance/memory/eval/schema_version are deliberately never authored here -- they're
inherited via the existing base_agent.yaml <- neighbourhood <- role merge
(factory/loader.py), so a compiled agent is governed by construction, not by this
function remembering to include the right fields.
"""

from __future__ import annotations

from madras.models.agent_spec import AgentSpec


def emit_role(spec: AgentSpec) -> dict[str, object]:
    return {
        "name": spec.name,
        "archetype": spec.archetype,
        "neighborhood": spec.neighborhood,
        "rank": "intern",
        "origin": "immigrant",
        "persona": {
            "voice": spec.persona_voice,
            "refusal_style": spec.persona_refusal_style,
            "north_star": spec.persona_north_star,
        },
        "capability_summary": spec.discovery_summary,
        "capabilities": list(spec.capabilities),
        "skills": list(spec.skills),
        "execution": {"default_pattern": spec.execution},
    }
