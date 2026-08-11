from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from madras.eval_.proving_ground.scenario import (
    Scenario,
    filter_partition,
    load_scenarios,
)


class Case(BaseModel):
    id: str
    suite_id: str
    benchmark_family: str
    features: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    prompt: str
    setup: dict[str, Any] = Field(default_factory=dict)
    checks: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    rubric: str = ""
    optimal_steps: int | None = None
    k: int = 3


class Suite(BaseModel):
    id: str
    name: str
    version: str
    kind: Literal["external", "native", "dataset"]
    provenance: str = ""
    features: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)

    def load_cases(self) -> list[Case]:
        raise NotImplementedError

    def run(self, model: str, k: int, concurrency: int) -> Any:
        # External self-running adapters (e.g. τ²-bench) override this.
        raise NotImplementedError


def _scenario_to_case(scenario: Scenario, suite_id: str) -> Case:
    setup = scenario.setup
    tools = list(setup.get("tools", []))
    return Case(
        id=scenario.id,
        suite_id=suite_id,
        benchmark_family=scenario.benchmark_family,
        features=list(scenario.features),
        tools=tools,
        prompt=scenario.task,
        setup=setup,
        checks=list(scenario.checks),
        rubric=scenario.rubric,
        k=scenario.k,
    )


class NativeSuite(Suite):
    kind: Literal["external", "native", "dataset"] = "native"
    directory: str
    # None = the official-Index view (all scenarios). "public" = the open release;
    # "held_out" = the private anti-contamination partition.
    partition: str | None = None

    def load_cases(self) -> list[Case]:
        scenarios = filter_partition(load_scenarios(self.directory), self.partition)
        cases = [_scenario_to_case(s, self.id) for s in scenarios]
        feats: list[str] = []
        tools: list[str] = []
        for c in cases:
            for f in c.features:
                if f not in feats:
                    feats.append(f)
            for t in c.tools:
                if t not in tools:
                    tools.append(t)
        self.features = feats
        self.tools = tools
        return cases
