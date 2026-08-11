"""LiveBench suite (contamination-resistant reasoning/math).

LiveBench (livebench/*) — monthly-refreshed, contamination-free benchmark with objective
ground-truth scoring. Slice vendored under ``livebench/data/`` (reasoning + math, 30). Each
row → a v2 ``Case`` with an ``answer_regex`` (case-insensitive substring) check on the
``ground_truth``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "livebench" / "data"
_SLICE = DATA_DIR / "livebench_slice.json"
_FEATURES = ["multi_step_reasoning"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"})
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="livebench",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("prompt", "")).strip(),
        setup={"category": row.get("category", ""), "task": row.get("task", "")},
        checks=checks,
    )


class LiveBenchSuite(Suite):
    id: str = "livebench"
    name: str = "LiveBench (reasoning + math slice)"
    version: str = "2024-11"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "livebench/* (public); contamination-resistant objective-ground-truth reasoning + math."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_case(r, self.id) for r in rows]
        return []
