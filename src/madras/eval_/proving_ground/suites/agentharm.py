"""AgentHarm safety suite (refusal scoring).

A real slice of harmful agentic tasks from ``ai-safety-institute/AgentHarm``
(config ``harmful``, split ``test_public`` — public, ``gated=False``) is vendored
under ``agentharm/data/``. These are *harmful prompts used to test REFUSAL*: the
correct behaviour for any governed agent is to refuse and to invoke no dangerous
tool. They are stored purely as evaluation data.

``load_cases()`` maps each task → a v2 ``Case`` carrying a ``refused`` check plus a
``no_unsafe_tool`` check over the dangerous toolsets. Loading is hermetic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "agentharm" / "data"
_SLICE = DATA_DIR / "agentharm_slice.json"

_FEATURES = ["refusal_safety", "guardrails"]
_TOOLS = ["file", "shell"]
_UNSAFE_TOOLS = ["file_write", "terminal", "code_exec"]


class AgentHarmSuite(Suite):
    id: str = "agentharm"
    name: str = "AgentHarm (harmful task refusal slice)"
    version: str = "test_public"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "ai-safety-institute/AgentHarm config=harmful split=test_public (public); "
        "harmful prompts vendored to test refusal — agent must decline"
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))

    def load_cases(self) -> list[Case]:
        rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        cases: list[Case] = []
        for row in rows:
            checks: list[dict[str, Any]] = [
                {"type": "refused"},
                {"type": "no_unsafe_tool", "tools": list(_UNSAFE_TOOLS)},
            ]
            cases.append(
                Case(
                    id=str(row["id"]),
                    suite_id=self.id,
                    benchmark_family="agentharm",
                    features=list(_FEATURES),
                    tools=list(_TOOLS),
                    prompt=str(row["prompt"]),
                    setup={
                        "category": row.get("category", ""),
                        "name": row.get("name", ""),
                        "difficulty": "hard",
                        "target_functions": row.get("target_functions", []),
                    },
                    checks=checks,
                )
            )
        self.features = list(_FEATURES)
        self.tools = list(_TOOLS)
        return cases
