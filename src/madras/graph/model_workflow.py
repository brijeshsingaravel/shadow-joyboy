"""Model-authored orchestration as one durable step — the `Workflow` step (row 92).

The model orchestrates its OWN subagents: it authors a workflow (a fan-out of subagent tasks, the
way it would author CodeAct Python), and the runtime executes the whole thing as ONE governed,
durable step. The lift over raw CodeAct + delegation is the governance envelope — the model's
orchestration can't become an unbounded fork-bomb: spawns are **budget-bounded** (the delegation
TurnBudget) and **depth-bounded** (the delegation contract's MAX_DEPTH), every subagent outcome is
traced. Composes `tools/delegation_context.TurnBudget` + an injected `delegate` primitive (the real
delegation call / a CodeAct-spawned child in prod); pure + injectable here. CodeAct's one-step
`result()` philosophy, applied to orchestration.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class WorkflowStep:
    task: str
    role: str = "worker"
    label: str = ""


@dataclass
class ModelWorkflow:
    steps: list[WorkflowStep] = field(default_factory=list[WorkflowStep])
    mode: str = "sequential"  # sequential | parallel (the model's fan-out shape)


@dataclass
class StepOutcome:
    label: str
    ok: bool
    result: Any = None
    reason: str = ""


@dataclass
class WorkflowResult:
    results: list[Any] = field(default_factory=list[Any])
    trace: list[StepOutcome] = field(default_factory=list[StepOutcome])
    halted: bool = False
    reason: str = ""


def run_workflow(
    workflow: ModelWorkflow,
    *,
    delegate: Callable[[str, str], Any],
    budget: Any,
    max_depth: int = 2,
    depth: int = 0,
) -> WorkflowResult:
    """Execute a model-authored workflow as one governed durable step.

    `delegate(task, role) -> result` is the injected delegation primitive. `budget` is a TurnBudget
    (`can_spawn`/`charge`). Halts (does not raise) when the depth bound is hit or the budget is
    exhausted, so the partial trace is still returned.
    """
    result = WorkflowResult()
    if depth >= max_depth:
        result.halted = True
        result.reason = f"max delegation depth {max_depth} reached"
        return result
    for index, step in enumerate(workflow.steps):
        label = step.label or f"step-{index}"
        if not budget.can_spawn(1):
            result.halted = True
            result.reason = "turn budget exhausted"
            result.trace.append(StepOutcome(label, ok=False, reason="budget exhausted"))
            break
        budget.charge(1)
        try:
            value = delegate(step.task, step.role)
            result.results.append(value)
            result.trace.append(StepOutcome(label, ok=True, result=value))
        except Exception as exc:  # a failed subagent is recorded, not fatal to the whole step
            result.trace.append(StepOutcome(label, ok=False, reason=f"{type(exc).__name__}: {exc}"))
    return result
