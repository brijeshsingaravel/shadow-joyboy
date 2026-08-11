"""compiler/verify.py — verify a compiled agent on the Proving Ground gate (E1 Task C1).

Composes already-built primitives, no new eval logic: runner.run_scenario() already
scores deterministically internally (score_deterministic(), zero-cost, no judge/LLM
call needed for a light gate) and gate.evaluate_gate() is the existing pass/fail
wrapper. The real work is (1) scenario selection -- a compiled agent's toolsets gate
which of the real native scenarios (scenarios/*.json) it can even attempt, and (2)
binding the compiled AgentRecord into the Proving Ground's OWN AgentSpec, aliased
PGAgentSpec here to avoid a real naming collision with models.agent_spec.AgentSpec
(found during grounding -- two different classes, same name, different purposes).

Fail-closed (matches score_deterministic's own doctrine, "no checks -> fail"): zero
eligible scenarios (a capability-starved agent) means passed=False, never a vacuous
pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from madras.eval_.proving_ground.agents import AgentSpec as PGAgentSpec
from madras.eval_.proving_ground.gate import evaluate_gate
from madras.eval_.proving_ground.runner import run_scenario
from madras.eval_.proving_ground.scenario import Scenario, load_scenarios
from madras.factory.spawn import AgentRecord
from madras.llm.gateway import LLMGateway

SCENARIOS_DIR = Path(__file__).resolve().parents[1] / "eval_" / "proving_ground" / "scenarios"


@dataclass
class VerifyResult:
    passed: bool
    index: float
    per_gate: dict[str, float]
    failures: list[str]


def select_light_scenarios(
    scenarios: list[Scenario], *, agent_toolsets: list[str] | None, limit: int = 5
) -> list[Scenario]:
    """Scenarios whose declared tool requirement is a subset of what the agent has."""
    agent_set = set(agent_toolsets or [])
    eligible = [s for s in scenarios if set(s.setup.get("tools", []) or []) <= agent_set]
    return eligible[:limit]


def _to_pg_agent(record: AgentRecord) -> PGAgentSpec:
    config = record.config
    return PGAgentSpec(
        id=config.name,
        label=config.display_name or config.name,
        agent_name=config.name,
        rank=config.rank,
        toolsets=list(config.toolsets) or None,
        persona=config.persona.voice if config.persona else None,
    )


async def verify_agent(
    record: AgentRecord, gateway: LLMGateway, model: str, *, limit: int = 5, target: float = 0.5
) -> VerifyResult:
    scenarios = load_scenarios(SCENARIOS_DIR)
    selected = select_light_scenarios(scenarios, agent_toolsets=record.config.toolsets, limit=limit)

    pg_agent = _to_pg_agent(record)
    scores: dict[str, float] = {}
    for scenario in selected:
        run = await run_scenario(
            scenario,
            gateway=gateway,
            model=model,
            toolsets=record.config.toolsets,
            agent=pg_agent,
        )
        scores[scenario.id] = run.pass_rate

    if not scores:
        return VerifyResult(passed=False, index=0.0, per_gate={}, failures=[])

    targets = {sid: target for sid in scores}
    report = evaluate_gate(scores, targets)
    failures = [item.benchmark for item in report.items if not item.passed]
    index = sum(scores.values()) / len(scores)
    return VerifyResult(passed=report.passed, index=index, per_gate=scores, failures=failures)
