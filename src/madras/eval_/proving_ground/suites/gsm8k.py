"""GSM8K suite (public).

GSM8K (``openai/gsm8k``, config ``main``, ``test`` split) is a public dataset of
grade-school math word problems. Each row has a ``question`` and an ``answer``
whose chain-of-thought ends with the canonical ``#### <number>`` marker giving the
final numeric answer.

A committed slice under ``gsm8k/data/`` makes ``load_cases()`` hermetic; absent it,
the test split is fetched live (no token required). Each row → a v2 ``Case`` with a
``multi_step_reasoning`` feature and an ``answer_regex`` check that matches the final
number as a standalone token (so "18" doesn't spuriously match "180").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "gsm8k" / "data"
_SLICE = DATA_DIR / "gsm8k_slice.json"

_FEATURES = ["multi_step_reasoning"]
_DATASET = "openai/gsm8k"
_CONFIG = "main"
_SPLIT = "test"

_MARKER_RE = re.compile(r"####\s*(.+?)\s*$", re.DOTALL)


def _final_answer(answer: str) -> str:
    """Extract the final number from a GSM8K answer's ``#### <number>`` marker."""
    m = _MARKER_RE.search(answer or "")
    if not m:
        return ""
    return m.group(1).strip().replace(",", "").replace("$", "")


def _row_to_case(row: dict[str, Any], suite_id: str, index: int = 0) -> Case:
    question = str(row.get("question", "")).strip()
    final = _final_answer(str(row.get("answer", "")))
    checks: list[dict[str, Any]] = []
    if final:
        # Match the number as a standalone token (avoids 18 matching 180/1018).
        checks.append({"type": "answer_regex", "pattern": rf"(?<!\d){re.escape(final)}(?!\d)"})
    return Case(
        id=str(row.get("id") or f"gsm8k_test_{index}"),
        suite_id=suite_id,
        benchmark_family="gsm8k",
        features=list(_FEATURES),
        tools=[],
        prompt=question,
        setup={"final_answer": final},
        checks=checks,
    )


class Gsm8kSuite(Suite):
    id: str = "gsm8k"
    name: str = "GSM8K (main test slice)"
    version: str = _CONFIG
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "openai/gsm8k (public, config=main, split=test); grade-school math word problems"
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id, i) for i, r in enumerate(rows)]

        from datasets import load_dataset  # type: ignore[import-untyped]  # heavy import

        ds = load_dataset(_DATASET, _CONFIG, split=_SPLIT)
        live: list[dict[str, Any]] = [dict(r) for r in ds]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        return [_row_to_case(r, self.id, i) for i, r in enumerate(live)]
