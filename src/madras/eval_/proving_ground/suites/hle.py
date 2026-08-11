"""HLE suite (Humanity's Last Exam — frontier knowledge).

HLE (cais/hle, gated — accepted) — extremely hard expert questions (exactMatch + multipleChoice).
Text-only slice vendored under ``hle/data/`` (30: 24 exactMatch + 6 MCQ; multimodal items
excluded). Each → a v2 ``Case`` with an ``answer_regex`` check on the gold answer (MCQ = the
letter with a word boundary; exactMatch = case-insensitive substring of the answer).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "hle" / "data"
_SLICE = DATA_DIR / "hle_slice.json"
_FEATURES = ["frontier_knowledge", "multi_step_reasoning"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("answer", "")).strip()
    is_mcq = str(row.get("answer_type", "")) == "multipleChoice"
    if is_mcq and len(answer) <= 3:
        pattern = rf"(?i)\b{re.escape(answer)}\b"
    else:
        pattern = rf"(?i){re.escape(answer)}"
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="hle",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("question", "")).strip(),
        setup={"answer_type": row.get("answer_type", ""), "category": row.get("category", "")},
        checks=[{"type": "answer_regex", "pattern": pattern}] if answer else [],
    )


class HleSuite(Suite):
    id: str = "hle"
    name: str = "Humanity's Last Exam (text slice)"
    version: str = "test"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = "cais/hle (gated, accepted); frontier expert questions — text-only slice."
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_case(r, self.id) for r in rows]
        return []
