"""Eval contract — 8 independent dimension gates, tiered signal emission,
Agent-as-Judge triggers. Per BASE_AGENT_SCHEMA.md §5.

Critical: per-dimension thresholds, NOT composite scoring. Research
evidence (Future AGI 2026) confirms composites hide failures.
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

REQUIRED_RANK_DIMENSIONS = {
    "task_completion",
    "correction_absorption",
    "clarification_quality",
    "confidence_calibration",
    "user_rating",
    "tool_selection",
    "argument_correctness",
    "error_recovery",
}


class JudgeMode(str, Enum):
    LLM_AS_JUDGE = "llm_as_judge"
    AGENT_AS_JUDGE = "agent_as_judge"


class AgentJudgeTrigger(str, Enum):
    PROMOTION_GATE = "promotion_gate"
    DEMOTION_REVIEW = "demotion_review"
    RED_TEAM_RELEASE_GATE = "red_team_release_gate"
    ASI_AUDIT = "asi_audit"
    MARKETPLACE_LISTING = "marketplace_listing"


class SignalTier(BaseModel):
    """Three emission tiers per the contract."""

    model_config = ConfigDict(extra="forbid")

    per_action_required: list[str] = Field(min_length=1)
    conditional_when_triggered: list[str] = Field(default_factory=list)
    session_rollup_required: list[str] = Field(default_factory=list)


class RankDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    threshold: float = Field(..., ge=0.0, le=1.0)
    metric: str | None = Field(default=None, description="Custom metric, e.g. ece_inverse")


class ThresholdCalibration(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bootstrap_phase_sessions: int = Field(..., ge=1)
    recalibration_cadence: Literal["weekly", "monthly", "quarterly", "yearly"]
    policy: Literal["hold_top_quartile_promotable", "hold_top_decile", "fixed"]
    audit_log: bool = True


class JudgesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routine_scoring: JudgeMode = JudgeMode.LLM_AS_JUDGE
    agent_as_judge_triggers: list[AgentJudgeTrigger] = Field(min_length=1)


class EvalContract(BaseModel):
    """The full eval contract that every agent inherits via base_agent.yaml."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str
    signals: SignalTier
    rank_dimensions: dict[str, RankDimension]
    promotion_rule: Literal["all_dimensions_clear_threshold"]
    sustained_over_sessions: int = Field(..., ge=1)
    demotion_floor: float = Field(..., ge=0.0, le=1.0)
    threshold_calibration: ThresholdCalibration
    judges: JudgesConfig

    @model_validator(mode="after")
    def _all_eight_dimensions_present(self) -> EvalContract:
        present = set(self.rank_dimensions)
        missing = REQUIRED_RANK_DIMENSIONS - present
        if missing:
            raise ValueError(
                f"missing required rank dimensions: {sorted(missing)}; "
                f"all eight are independent gates per §5"
            )
        return self

    @model_validator(mode="after")
    def _demotion_floor_below_thresholds(self) -> EvalContract:
        min_threshold = min(rd.threshold for rd in self.rank_dimensions.values())
        if self.demotion_floor >= min_threshold:
            raise ValueError(
                f"demotion_floor ({self.demotion_floor}) must be strictly below "
                f"the lowest rank dimension threshold ({min_threshold})"
            )
        return self
