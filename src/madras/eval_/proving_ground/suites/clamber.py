"""CLAMBER suite (clarify / ambiguity).

CLAMBER (zt991211/CLAMBER) — a benchmark of identifying + clarifying ambiguous information
needs. Slice vendored under ``clamber/data/`` = 30 **ambiguous** cases (gold
``require_clarification=1``): the agent SHOULD ask a clarifying question rather than answer
blindly. Each → a v2 ``Case`` whose check passes when the response **asks a clarifying
question** (Madras's clarify capability — the site shows CLAMBER as a weak axis to climb).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "clamber" / "data"
_SLICE = DATA_DIR / "clamber_slice.json"
_FEATURES = ["clarification", "ambiguity_detection"]
# response must read as a clarifying question, not a blind answer
_ASK_RE = (
    r"(?i)(\?|could you|do you mean|did you mean|which (one|kind|type)|"
    r"what (kind|type|specific)|are you looking|please (specify|clarify)|"
    r"to clarify|can you (specify|tell me))"
)


def _case(row: dict[str, Any], suite_id: str, i: int) -> Case:
    return Case(
        id=f"clamber_{i}",
        suite_id=suite_id,
        benchmark_family="clamber",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("question", "")).strip(),
        setup={"require_clarification": True, "category": row.get("category", "")},
        checks=[{"type": "answer_regex", "pattern": _ASK_RE}],
    )


class ClamberSuite(Suite):
    id: str = "clamber"
    name: str = "CLAMBER (clarify ambiguity)"
    version: str = "benchmark"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "zt991211/CLAMBER (public); ambiguous-query identification + clarification "
        "— ambiguous (should-ask) slice."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_case(r, self.id, i) for i, r in enumerate(rows)]
        return []
