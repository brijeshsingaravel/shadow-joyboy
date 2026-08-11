"""abg-CoQA suite (clarify-under-ambiguity).

abg-CoQA (MeiqiGuo/AKBC2021-Abg-CoQA, MIT) - conversational QA where the target question may be
AMBIGUOUS given the story+history, so the agent should ask a clarifying question rather than guess.
Judge-scored against an ambiguity-aware rubric. Full set loads from the local clone if present, else
the vendored slice. Clarify dimension; held-out gate set. W0-A2.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "abgcoqa" / "data"
_SLICE = DATA_DIR / "abgcoqa_slice.json"
_CLONE = (
    Path(__file__).resolve().parents[5]
    / ".benchmarks"
    / "_outlier_src"
    / "abgcoqa"
    / "abg-coqa"
    / "coqa_abg_test.json"
)
_FEATURES = ["clarify"]


def _rubric(ambiguous: bool) -> str:
    if ambiguous:
        return (
            "The target question is AMBIGUOUS given the story+history. PASS if the agent asks "
            "a clarifying question instead of guessing. FAIL if it answers directly."
        )
    return (
        "The target question is CLEAR. PASS if the agent answers directly. FAIL if it asks an "
        "unnecessary clarifying question."
    )


def _case(row: dict[str, Any], suite_id: str) -> Case:
    story = str(row.get("story", "")).strip()
    q = str(row.get("question", "")).strip()
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="abgcoqa",
        features=list(_FEATURES),
        tools=[],
        prompt=f"{story}\n\nQuestion: {q}",
        rubric=_rubric(bool(row.get("ambiguous"))),
        setup={"ambiguous": bool(row.get("ambiguous")), "split": "heldout"},
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        data = json.loads(_CLONE.read_text(encoding="utf-8"))["data"]
        out: list[dict[str, Any]] = []
        for i, d in enumerate(data):
            tt = d["target_turn"]
            tt = cast("dict[str, Any]", tt if isinstance(tt, dict) else ast.literal_eval(tt))
            q = tt.get("question", "")
            if q:
                out.append(
                    {
                        "id": f"abgcoqa_{i}",
                        "story": d["story"],
                        "question": q,
                        "ambiguous": d.get("ambiguity") == "ambiguous",
                    }
                )
        return out
    except Exception:
        return None


class AbgCoqaSuite(Suite):
    id: str = "abgcoqa"
    name: str = "abg-CoQA (clarify-under-ambiguity)"
    version: str = "test"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "MeiqiGuo/AKBC2021-Abg-CoQA (MIT); ambiguous conversational questions, "
        "judge-scored on ask-vs-answer. Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
