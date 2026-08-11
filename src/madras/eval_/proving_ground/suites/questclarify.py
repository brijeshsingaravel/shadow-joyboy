"""QuestClarify suite (identify-the-missing-variable clarify - Madras-original held-out).

In the style of QuestBench (which ships only a generator, no static data): an under-specified ask
has exactly ONE missing variable the agent should ASK for, mixed with fully-specified controls it
should just answer. Judge-scored on ask-the-right-thing vs guess (and don't over-ask on clear ones).
Held-out gate set. W0-A3.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "questclarify" / "data"
_SLICE = DATA_DIR / "questclarify_slice.json"
_FEATURES = ["clarify"]


def _rubric(missing: str) -> str:
    if missing == "none":
        return (
            "The request is fully specified. PASS if the agent answers directly. FAIL if it "
            "asks an unnecessary clarifying question."
        )
    return (
        f"The request is under-specified: the missing variable is the {missing}. PASS if the "
        f"agent asks for the {missing}. FAIL if it guesses or proceeds without asking."
    )


def _case(row: dict[str, Any], suite_id: str) -> Case:
    missing = str(row.get("missing", "none"))
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="questclarify",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("prompt", "")).strip(),
        rubric=_rubric(missing),
        setup={"missing": missing, "split": "heldout"},
    )


class QuestClarifySuite(Suite):
    id: str = "questclarify"
    name: str = "QuestClarify (identify the missing variable)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "native"
    provenance: str = (
        "Madras-original held-out (QuestBench-style; QuestBench ships only a "
        "generator). Ask-the-missing-variable vs guess. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if not _SLICE.exists():
            return []
        return [_case(r, self.id) for r in json.loads(_SLICE.read_text(encoding="utf-8"))]
