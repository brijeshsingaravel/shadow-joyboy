"""compiler/simulate.py — § B5 Simulation Playground: run the just-compiled agent for
real on one sample task, before it's ever deployed (D38's compile -> simulate -> verify
closed loop). Reuses the real production tool-execution graph (`graph.build.build_llm_graph`,
tools_on=True) -- the same path a deployed agent runs through -- so nothing here is a
mock or a second, parallel execution engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from madras.compiler.turn import run_agent_turn
from madras.factory.spawn import AgentRecord
from madras.llm.gateway import LLMGateway
from madras.llm.structured import structured_output


@dataclass
class SimulationStep:
    kind: str  # "thought" | "action" | "result"
    label: str


@dataclass
class SimulationResult:
    sample_task: str
    steps: list[SimulationStep]
    cost_usd: float
    passed: bool
    verdict_reason: str


_SAMPLE_TASK_SCHEMA = {
    "type": "object",
    "required": ["task"],
    "properties": {"task": {"type": "string"}},
}


async def generate_sample_task(*, outcome: str, gateway: LLMGateway, model: str) -> str:
    """One small structured call: a single realistic sample request this agent might
    receive, given what it was built for. Clearly sample data, never real user data."""
    messages = [
        {
            "role": "system",
            "content": (
                "You write ONE short, realistic sample task a user might send to an "
                "agent built for the given outcome. Return only the task text a user "
                "would type -- no preamble, no explanation."
            ),
        },
        {"role": "user", "content": f"Agent outcome: {outcome}"},
    ]
    result = await structured_output(gateway, model, messages, _SAMPLE_TASK_SCHEMA, max_tokens=200)
    if result.ok and result.data and result.data.get("task"):
        return str(result.data["task"])
    return outcome  # honest fallback: re-use the outcome itself as the sample task


async def simulate_agent(
    *, agent: AgentRecord, outcome: str, gateway: LLMGateway, model: str, audit: Any = None
) -> SimulationResult:
    """`audit`: an AuditLogWriter (or None) -- when given, every tool call this
    simulation makes writes a REAL immutable audit row, exactly like a deployed agent
    would (§ B7's Workspace activity feed reads from the same table)."""
    sample_task = await generate_sample_task(outcome=outcome, gateway=gateway, model=model)

    turn = await run_agent_turn(
        agent=agent,
        user_input=sample_task,
        gateway=gateway,
        model=model,
        session_id="simulate",
        audit=audit,
    )

    steps: list[SimulationStep] = [SimulationStep(kind="thought", label=sample_task)]
    for tool_name in turn.tool_calls:
        steps.append(SimulationStep(kind="action", label=tool_name))
    steps.append(SimulationStep(kind="result", label=turn.response_text))

    return SimulationResult(
        sample_task=sample_task,
        steps=steps,
        cost_usd=turn.cost_usd,
        passed=turn.passed,
        verdict_reason="produced a real response" if turn.passed else "no response came back",
    )
