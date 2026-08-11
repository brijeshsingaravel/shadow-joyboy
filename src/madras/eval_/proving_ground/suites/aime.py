"""AIME suite (public competition math).

AIME (American Invitational Mathematics Examination) — hard competition problems whose
answer is always an integer 0-999. Slice: ``Maxwell-Jia/AIME_2024`` (30 problems), vendored
under ``aime/data/`` so ``load_cases()`` is hermetic (live fetch as fallback, no token). Each
row → a v2 ``Case`` with ``multi_step_reasoning`` + an ``answer_regex`` check matching the
integer answer as a standalone token (so "33" doesn't match "330").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "aime" / "data"
_SLICE = DATA_DIR / "aime_slice.json"
_FEATURES = ["multi_step_reasoning"]
_DATASET = "Maxwell-Jia/AIME_2024"


def _row_to_case(row: dict[str, Any], suite_id: str, index: int = 0) -> Case:
    answer = str(row.get("answer", "")).strip().replace(",", "")
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_regex", "pattern": rf"(?<!\d){re.escape(answer)}(?!\d)"})
    return Case(
        id=str(row.get("id") or f"aime_{index}"),
        suite_id=suite_id,
        benchmark_family="aime",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("question", "")).strip() + "\n\nGive the final integer answer (0-999).",
        setup={"final_answer": answer},
        checks=checks,
    )


class AimeSuite(Suite):
    id: str = "aime"
    name: str = "AIME 2024 (competition math)"
    version: str = "2024"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = "Maxwell-Jia/AIME_2024 (public); integer-answer competition math."
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id, i) for i, r in enumerate(rows)]
        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset(_DATASET, split="train")
        return [
            _row_to_case(
                {"id": r["ID"], "question": r["Problem"], "answer": r["Answer"]}, self.id, i
            )
            for i, r in enumerate(ds)
        ]
