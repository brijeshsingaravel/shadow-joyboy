from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

# Anti-contamination split (P3): the OPEN release ships only "public" scenarios;
# the official Madras Index also scores the private "held_out" partition, which is
# never released. New scenarios default to public unless explicitly held back.
PUBLIC = "public"
HELD_OUT = "held_out"
Partition = Literal["public", "held_out"]


class Scenario(BaseModel):
    id: str
    benchmark_family: str  # BFCL | tau2 | AgentHarm | GAIA | LongMemEval | Madras
    features: list[str]  # Shadow features this exercises (matrix keys)
    topic: str
    task: str  # the user message Shadow receives
    setup: dict[str, Any] = Field(default_factory=dict)  # seed memory/files/mode/tools
    checks: list[dict[str, Any]] = Field(
        default_factory=list[dict[str, Any]]
    )  # deterministic checks
    rubric: str = ""  # judge rubric (holistic dimensions)
    difficulty: Literal["easy", "med", "hard"] = "med"
    partition: Partition = PUBLIC  # "public" (open release) | "held_out" (private Index)
    k: int = 3  # pass^k resamples


def load_scenarios(directory: str | Path) -> list[Scenario]:
    d = Path(directory)
    out: list[Scenario] = []
    for p in sorted(d.glob("*.json")):
        out.append(Scenario.model_validate(json.loads(p.read_text(encoding="utf-8"))))
    return out


def filter_partition(scenarios: list[Scenario], partition: str | None) -> list[Scenario]:
    """Keep only scenarios in `partition`; None = all (the official-Index view)."""
    if partition is None:
        return list(scenarios)
    return [s for s in scenarios if s.partition == partition]
