"""Supervisor — conditional outcome-gate at MVP, PRM slot reserved (§9)."""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class SupervisorMode(str, Enum):
    ALWAYS = "always"
    CONDITIONAL = "conditional"
    HIGH_STAKES_ONLY = "high_stakes_only"


class SupervisorOutputMode(str, Enum):
    OUTCOME_GATE = "outcome_gate"
    STEP_REWARD = "step_reward"  # reserved for Phase 2 PRM


class PrmSlot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    training_data_source: Optional[str] = None
    coaching_target: Optional[str] = None


class SupervisorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    mode: SupervisorMode = SupervisorMode.CONDITIONAL
    triggers: list[str] = Field(default_factory=list)
    max_latency_budget_ms: int = Field(default=300, ge=10, le=10000)
    output_mode: SupervisorOutputMode = SupervisorOutputMode.OUTCOME_GATE
    phase_2_prm: PrmSlot = Field(default_factory=PrmSlot)
