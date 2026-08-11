"""Execution pattern — 3-tier ReAct / Plan-Execute / Reflexion (§6)."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExecutionPattern(str, Enum):
    REACT = "react"
    PLAN_EXECUTE = "plan_execute"
    REFLEXION = "reflexion"


class ExecutionMode(str, Enum):
    """How acts are expressed: textual tool-call JSON, or CodeAct (Python in the sandbox)."""

    TEXTUAL = "textual"
    CODEACT = "codeact"


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_pattern: ExecutionPattern
    escalation_rules: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    max_steps: int = Field(..., ge=1, le=1000)
    max_tool_calls_per_step: int = Field(..., ge=1, le=100)
    max_recursion_depth: int = Field(..., ge=1, le=10)
    # CodeAct (D16) — PG-gated: keep per-agent whichever beats textual on the Index. Default
    # textual so nothing changes until an agent is explicitly switched + measured.
    mode: ExecutionMode = ExecutionMode.TEXTUAL
    # Action self-critique/retry (Reflexion at the action level). When on, a failed action gets
    # one bounded critique-and-correct pass before surfacing the failure.
    self_critique: bool = False
    self_critique_max_retries: int = Field(default=1, ge=0, le=3)
    # s46: Judgment Engine -- one cheap judge call bias-checks a repeated_failure impasse
    # (confirmation/sunk-cost/recency/halo) before the agent retries. Off by default, same
    # per-agent opt-in pattern as self_critique above (an extra LLM call per impasse).
    judgment_engine: bool = False
    # s46: Identity Anchor -- one cheap judge call checks the turn's response against
    # CONSTITUTION.md's Prime Directives. Off by default, same opt-in pattern.
    integrity_monitor: bool = False
