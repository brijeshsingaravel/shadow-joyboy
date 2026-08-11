"""LoCoMo suite (long-conversational memory).

LoCoMo (snap-research/locomo) — very-long-term multi-session conversations with QA over them;
the canonical long-context / agent-memory recall benchmark. Slice vendored under ``locomo/data/``
(2 conversations x ~15 answerable QAs; conversations stored once, ~63K chars each — genuinely
long). Each QA → a v2 ``Case`` whose prompt is the full conversation + the question, with an
``answer_regex`` (case-insensitive substring) check on the gold answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "locomo" / "data"
_SLICE = DATA_DIR / "locomo_slice.json"
_SRC = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"
_FEATURES = ["long_context_recall", "memory"]


def _case(qa: dict[str, Any], conv: str, suite_id: str) -> Case:
    answer = str(qa.get("answer", "")).strip()
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_regex", "pattern": rf"(?i){re.escape(answer)}"})
    prompt = (
        "Read this long multi-session conversation, then answer the question from it.\n\n"
        f"{conv}\n\n=== QUESTION ===\n{qa.get('question', '')}\n"
        "Answer concisely from the conversation."
    )
    return Case(
        id=str(qa.get("id")),
        suite_id=suite_id,
        benchmark_family="locomo",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"gold_answer": answer},
        checks=checks,
    )


class LoCoMoSuite(Suite):
    id: str = "locomo"
    name: str = "LoCoMo (long-conversational memory)"
    version: str = "locomo10"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "snap-research/locomo (public); multi-session long-term conversation QA "
        "— long-context/memory recall."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            blob = json.loads(_SLICE.read_text(encoding="utf-8"))
            convs = blob["conversations"]
            return [_case(qa, convs.get(qa["conv_id"], ""), self.id) for qa in blob["qas"]]
        return []  # live fetch is large; the vendored slice is the hermetic source
