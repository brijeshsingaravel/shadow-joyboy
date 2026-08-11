"""Agent registry — the unit-under-test dimension for the Proving Ground.

An eval run is no longer just a *model* over cases; it is an **agent** (a governed
configuration — rank, toolsets, persona) running on a *model*. The sweep executes
the cross-product ``agents x models x cases`` and the store keys every row on both
``agent`` and ``model`` so the leaderboard, coverage matrix, and regression gate
can be sliced by *who* (agent), *on what* (model), and *for which use case*
(benchmark / feature).

This is the lightweight binding: each ``AgentSpec`` declares exactly what the
governed loop needs (``rank``, ``toolsets``, ``persona``, ``agent_name``) plus the
models it is meant to run on. Shadow is the first entry. A future agent is one
more ``AgentSpec`` — and the same spec can later be backed by ``factory/spawn.py``
without changing the eval surface.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from madras.models.agent_config import Rank


class AgentSpec(BaseModel):
    """A governed agent configuration the eval can drive end-to-end.

    ``toolsets=None`` means "every toolset the agent's rank allows" (the runner's
    default); a list restricts the agent to those bundles. ``persona`` overrides
    the loop's system prompt when set. ``default_models`` seeds the UI / a no-model
    sweep so an agent always has something sensible to run on.
    """

    id: str
    label: str
    agent_name: str
    rank: Rank = Rank.PRINCIPAL
    toolsets: list[str] | None = None
    persona: str | None = None
    default_models: list[str] = Field(default_factory=lambda: ["llama-70b"])


AGENTS: dict[str, AgentSpec] = {
    "shadow": AgentSpec(
        id="shadow",
        label="Shadow",
        agent_name="shadow",
        rank=Rank.PRINCIPAL,
        toolsets=None,
        persona=None,
        default_models=["llama-70b"],
    ),
}

# The agent assumed for back-compat (pre-agent-dimension rows default to this).
DEFAULT_AGENT = "shadow"


def load_agent(agent_id: str) -> AgentSpec:
    """Return the registered agent for ``agent_id`` (raises ``KeyError`` if unknown)."""
    return AGENTS[agent_id]


def all_agents() -> list[AgentSpec]:
    """Every registered agent."""
    return list(AGENTS.values())


def resolve_agents(requested: list[str] | None) -> list[str]:
    """Agent ids to sweep: the explicit request (filtered to known ids) or all.

    An empty / unknown request falls back to every registered agent so a sweep
    never runs zero agents.
    """
    if requested:
        known = [a for a in requested if a in AGENTS]
        if known:
            return known
    return list(AGENTS)
