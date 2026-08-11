"""EpMemory suite (episodic / chronological memory - Madras-original held-out).

In the style of EpBench (an LLM-driven generator, no static data): a dated timeline of events, then
questions about what happened when and in what order. Case-insensitive ``answer_regex`` on the gold.
Held-out gate set. W0-A3.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "epmemory" / "data"
_SLICE = DATA_DIR / "epmemory_slice.json"
_FEATURES = ["memory_recall", "multi_step_reasoning"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    checks = [{"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"}] if answer else []
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="epmemory",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("prompt", "")).strip(),
        setup={"gold_answer": answer, "split": "heldout"},
        checks=checks,
    )


class EpMemorySuite(Suite):
    id: str = "epmemory"
    name: str = "EpMemory (episodic / chronological)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "native"
    provenance: str = (
        "Madras-original held-out (EpBench-style; EpBench is a generator). "
        "Chronological-event recall + ordering. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if not _SLICE.exists():
            return []
        return [_case(r, self.id) for r in json.loads(_SLICE.read_text(encoding="utf-8"))]
