"""MMLU-Pro suite (public).

MMLU-Pro (``TIGER-Lab/MMLU-Pro``, ``test`` split) is a public multiple-choice
benchmark spanning broad academic knowledge across categories (business, physics,
law, health, etc.), with up to ten options per question. Each row carries a
``question``, an ``options`` list, an ``answer`` letter, and a 0-based
``answer_index`` into ``options``.

A committed slice under ``mmlu_pro/data/`` makes ``load_cases()`` hermetic; absent
it, the test split is fetched live (no token required). Each row → a v2 ``Case``
with a ``knowledge_reasoning`` feature: the lettered options are formatted into the
prompt, and the check is ``answer_contains`` against the correct option's text.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "mmlu_pro" / "data"
_SLICE = DATA_DIR / "mmlu_pro_slice.json"

_FEATURES = ["knowledge_reasoning"]
_DATASET = "TIGER-Lab/MMLU-Pro"
_SPLIT = "test"


def _options(row: dict[str, Any]) -> list[str]:
    raw = row.get("options")
    if not isinstance(raw, list):
        return []
    return [str(o).strip() for o in cast("list[Any]", raw)]


def _correct_option(row: dict[str, Any]) -> str:
    """Resolve the correct option's text, preferring ``answer_index`` then the letter."""
    options = _options(row)
    idx = row.get("answer_index")
    if isinstance(idx, int) and 0 <= idx < len(options):
        return options[idx]
    letter = str(row.get("answer", "")).strip().upper()
    if len(letter) == 1 and letter.isalpha():
        i = ord(letter) - 65
        if 0 <= i < len(options):
            return options[i]
    return ""


def _row_to_case(row: dict[str, Any], suite_id: str) -> Case:
    question = str(row.get("question", "")).strip()
    options = _options(row)
    correct = _correct_option(row)
    labelled = "\n".join(f"{chr(65 + i)}) {opt}" for i, opt in enumerate(options))
    prompt = f"{question}\n\nOptions:\n{labelled}" if options else question
    checks: list[dict[str, Any]] = []
    if correct:
        checks.append({"type": "answer_contains", "text": correct})
    return Case(
        id=str(row.get("question_id", "")),
        suite_id=suite_id,
        benchmark_family="mmlu_pro",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"category": row.get("category", ""), "answer": row.get("answer", "")},
        checks=checks,
    )


class MmluProSuite(Suite):
    id: str = "mmlu_pro"
    name: str = "MMLU-Pro (test slice)"
    version: str = "test"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = "TIGER-Lab/MMLU-Pro (public, split=test); broad multiple-choice knowledge"
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id) for r in rows]

        from datasets import load_dataset  # type: ignore[import-untyped]  # heavy import

        ds = load_dataset(_DATASET, split=_SPLIT)
        live: list[dict[str, Any]] = [dict(r) for r in ds]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        return [_row_to_case(r, self.id) for r in live]
