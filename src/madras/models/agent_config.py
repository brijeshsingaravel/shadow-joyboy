"""AgentConfig — the top-level Pydantic root.

Every field maps to a section in BASE_AGENT_SCHEMA.md. Sub-blocks are
defined in sibling modules and re-exported here.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from madras.models.eval import EvalContract
from madras.models.execution import ExecutionConfig
from madras.models.experiences import ExperiencesConfig
from madras.models.memory import MemoryConfig
from madras.models.persona import PersonaAnchoring, PersonaConfig
from madras.models.supervisor import SupervisorConfig
from madras.models.tools import SkillRef, ToolBundleRef


class Origin(str, Enum):
    NATIVE = "native"
    TOURIST = "tourist"
    IMMIGRANT = "immigrant"


class Rank(str, Enum):
    INTERN = "intern"
    JUNIOR = "junior"
    SPECIALIST = "specialist"
    SENIOR = "senior"
    PRINCIPAL = "principal"
    LEGEND = "legend"


_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


class AgentConfig(BaseModel):
    """Top-level agent contract. Validates against BASE_AGENT_SCHEMA.md.

    Sub-blocks (memory, eval, tools, persona, execution, supervisor,
    experiences) are attached in later tasks via Optional[...] fields,
    so this minimal version validates the identity block today.
    """

    model_config = ConfigDict(extra="forbid", use_enum_values=False)

    schema_version: str = Field(..., description="Pinned to BASE_AGENT_SCHEMA.md version")
    constitution_version: str = Field(..., description="Pinned to CONSTITUTION.md version")
    name: str = Field(..., description="Internal slug, snake_case")
    display_name: Optional[str] = Field(default=None, description="Human-facing name")
    introducer: Optional[str] = Field(default=None, description="How it introduces itself")
    capability_summary: Optional[str] = Field(
        default=None,
        description="Routing/discoverability signal for orchestrators + marketplace "
        "listings: when should THIS agent be invoked. Distinct from introducer (a "
        "conversational greeting) -- machine-facing, not chat-facing. Matches the "
        "LangGraph handoff-tool `description` / MCP manifest `description` convention.",
    )
    archetype: str = Field(..., description="Canonical role slug")
    neighborhood: str = Field(..., description="One of nine neighborhoods")
    rank: Rank
    origin: Origin
    memory: Optional[MemoryConfig] = Field(
        default=None,
        description="6-layer memory config; required after merge with base_agent.yaml",
    )
    eval: Optional[EvalContract] = Field(default=None, description="From base_agent.yaml")
    persona: Optional[PersonaConfig] = None
    persona_anchoring: Optional[PersonaAnchoring] = None
    execution: Optional[ExecutionConfig] = None
    supervisor: Optional[SupervisorConfig] = None
    tool_bundles: list[ToolBundleRef] = Field(default_factory=list[ToolBundleRef])
    toolsets: list[str] = Field(
        default_factory=list,
        description="Toolset names (registry.py toolset groups) this agent has by default, "
        "every session, unless the request explicitly overrides. Distinct from tool_bundles "
        "(sandboxed-exec bundles with their own credential policy).",
    )
    skills: list[SkillRef] = Field(default_factory=list[SkillRef])
    experiences: Optional[ExperiencesConfig] = None
    capabilities: list[str] = Field(
        default_factory=list,
        description="Capability Catalog ids declared by this agent (D35). Resolved into "
        "toolsets at load time via capabilities/resolve.py — additive to any toolsets "
        "already declared directly; does not replace tool_bundles/toolsets authoring.",
    )

    @field_validator("name")
    @classmethod
    def _name_snake_case(cls, v: str) -> str:
        if not _SNAKE_CASE_RE.match(v):
            raise ValueError(f"name must be snake_case: {v!r}")
        return v

    @model_validator(mode="after")
    def _display_name_default(self) -> AgentConfig:
        if self.display_name is None:
            self.display_name = self.name.replace("_", " ").title()
        return self
