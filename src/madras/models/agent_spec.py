"""AgentSpec — the Compiler's structured intermediate (E1 Task B1).

outcome + picked capabilities -> AgentSpec -> (B4 emit) -> a role-layer dict -> the
existing factory.loader/spawn path. Capabilities are Capability Catalog ids; when a
Catalog is passed via `model_validate(..., context={"catalog": catalog})`, they're
checked against the real catalog (unknown ids rejected). Without a catalog in context,
only shape is validated (unit-test convenience) — callers on the real compile path
always pass one.
"""

from __future__ import annotations

import re
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, ValidationInfo, field_validator

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The 9 real neighbourhoods (agents/neighborhoods/*.yaml stems) — matches Roster.md/
# City/Neighbourhoods.md. Hardcoded like Origin/Rank in agent_config.py.
NEIGHBOURHOODS = frozenset(
    {
        "tidel_park",
        "pondy_bazaar",
        "kollywood",
        "mount_road_merchant",
        "high_court_steps",
        "club_house_road",
        "marina_walk",
        "chennai_central",
        "theosophical_grounds",
    }
)

ExecutionPattern = Literal["react", "plan_execute", "reflexion"]


class AgentSpec(BaseModel):
    outcome: str = Field(..., min_length=1, description="The plain-language goal")
    name: str = Field(..., description="Internal slug, snake_case")
    archetype: str = Field(..., description="Canonical role slug")
    neighborhood: str = Field(..., description="One of the 9 real neighbourhoods")
    persona_voice: str = Field(..., description="How the agent sounds")
    persona_refusal_style: str = Field(
        ...,
        description="How the agent declines/pushes back — matches PersonaConfig's real "
        "required shape (models/persona.py) and Identity.md's session_start_anchor, which "
        "injects voice+refusal_style at turn 0 to fight persona drift. Not decorative.",
    )
    persona_north_star: str = Field(
        ...,
        description="The guiding narrative/worldview behind this agent — the third leg "
        "of PersonaConfig. Generated together with voice+refusal_style in one call so the "
        "three cohere as one character, not three disconnected traits (industry pattern: "
        "layered persona cards — voice, behavioral rules, backstory/worldview).",
    )
    discovery_summary: str = Field(
        ...,
        min_length=1,
        description="Routing signal: when should this agent be invoked? Distinct from "
        "persona_voice — used by a future multi-agent orchestrator / the Marketplace to "
        "pick the right agent, not to describe its personality.",
    )
    capabilities: list[str] = Field(
        default_factory=list, description="Capability Catalog ids this agent declares"
    )
    skills: list[str] = Field(default_factory=list)
    schedule: Optional[str] = None
    execution: ExecutionPattern

    @field_validator("outcome", "discovery_summary", "persona_refusal_style", "persona_north_star")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("must not be blank")
        return v

    @field_validator("name")
    @classmethod
    def _name_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE_RE.match(v):
            raise ValueError(f"name must be snake_case: {v!r}")
        return v

    @field_validator("neighborhood")
    @classmethod
    def _known_neighborhood(cls, v: str) -> str:
        if v not in NEIGHBOURHOODS:
            raise ValueError(f"unknown neighborhood: {v!r} (must be one of the 9)")
        return v

    @field_validator("capabilities")
    @classmethod
    def _capabilities_in_catalog(cls, v: list[str], info: ValidationInfo) -> list[str]:
        catalog: Any = info.context.get("catalog") if info.context else None
        if catalog is None:
            return v
        by_id: Any = catalog.by_id
        unknown = [cap_id for cap_id in v if cap_id not in by_id]
        if unknown:
            raise ValueError(f"capabilities not in the catalog: {unknown}")
        return v
