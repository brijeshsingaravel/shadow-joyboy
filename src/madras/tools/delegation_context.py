"""Per-run delegation context so the `delegate` tool can spawn governed child loops."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from madras.models.agent_config import Rank
from madras.tasks.durable_world import MemoryWorld

MAX_DEPTH = 2


@dataclass
class TurnBudget:
    """Shared across a whole delegation tree for one supervisor turn (a mutable
    object referenced by every DelegationCtx copy, so charges accumulate).

    NOTE: dataclasses.replace(ctx, depth=...) reuses the same TurnBudget reference
    (it copies the field value, which is a reference to the shared mutable object).
    This is intentional — all children in a turn must charge the same budget.
    """

    max_subagents: int
    max_cost_usd: float = 1.0
    spent_subagents: int = 0
    spent_cost_usd: float = 0.0
    # Leadership Engine: per-role competence history, scoped to THIS turn's delegation
    # tree (Swarm/Claude-Code-subagent pattern -- stateless handoff, no cross-session
    # trust leak). Round 2 of a delegate_team call benefits from round 1's track record;
    # unrelated turns/tests never see each other's history.
    competence_world: MemoryWorld = field(default_factory=MemoryWorld)

    def remaining(self) -> int:
        return max(0, self.max_subagents - self.spent_subagents)

    def can_spawn(self, n: int = 1) -> bool:
        return (
            self.spent_subagents + n
        ) <= self.max_subagents and self.spent_cost_usd < self.max_cost_usd

    def charge(self, n: int = 1, cost: float = 0.0) -> None:
        self.spent_subagents += n
        self.spent_cost_usd += cost


@dataclass
class DelegationCtx:
    gateway: Any  # LLMGateway
    registry: Any  # ToolRegistry
    executor: Any  # GovernedExecutor
    permission_engine: Any  # PermissionEngine
    base_model: str  # the supervisor's model (for tiering)
    agent_name: str
    session_id: str
    depth: int = 0
    budget: TurnBudget | None = None  # None → no per-turn enforcement (backward compat)
    agent_rank: Rank = Rank.INTERN  # the supervisor's rank (for governed batch re-entry)


_active: ContextVar[DelegationCtx | None] = ContextVar("madras_delegation_ctx", default=None)


def set_delegation_ctx(ctx: DelegationCtx | None) -> None:
    _active.set(ctx)


def get_delegation_ctx() -> DelegationCtx | None:
    return _active.get()
