"""GPQA suite (token-gated).

GPQA (``Idavidrein/gpqa``) is a *gated* HF dataset: fetching it requires a free
``HUGGINGFACE_TOKEN`` (read from the master vault via ``settings``, never hardcoded).
We use the ``gpqa_diamond`` config — the hard, expert-curated 198-question set of
graduate-level science (physics, chemistry, biology) multiple-choice problems.

Behaviour mirrors the GAIA suite:
- A committed slice under ``gpqa/data/`` is used when present (hermetic).
- Otherwise, **no token** → ``load_cases()`` returns ``[]`` and logs; **token
  present** → the ``gpqa_diamond`` train split is fetched live and mapped.

Each row → a v2 ``Case`` with a ``multi_step_reasoning`` feature. The four answer
options (1 correct + 3 incorrect) are formatted into the prompt; the check is
``answer_contains`` against the correct answer's text.
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "gpqa" / "data"
_SLICE = DATA_DIR / "gpqa_slice.json"

_FEATURES = ["multi_step_reasoning"]
_DATASET = "Idavidrein/gpqa"
_CONFIG = "gpqa_diamond"
_SPLIT = "train"


def _options(row: dict[str, Any], *, seed: str) -> list[str]:
    """Return the 4 answer texts with a DETERMINISTIC per-case shuffle.

    The raw dataset lists the correct answer first, so labelling in declaration
    order would always make the answer option ``A`` — a position-leak that lets a
    model score without reasoning. Shuffling per ``Record ID`` keeps the slice
    reproducible while removing the leak.
    """
    keys = ("Correct Answer", "Incorrect Answer 1", "Incorrect Answer 2", "Incorrect Answer 3")
    opts = [str(row.get(k, "")).strip() for k in keys if str(row.get(k, "")).strip()]
    random.Random(seed).shuffle(opts)
    return opts


def _row_to_case(row: dict[str, Any], suite_id: str) -> Case:
    question = str(row.get("Question", "")).strip()
    correct = str(row.get("Correct Answer", "")).strip()
    record_id = str(row.get("Record ID", row.get("record_id", "")))
    opts = _options(row, seed=record_id)
    labelled = "\n".join(f"{chr(65 + i)}) {opt}" for i, opt in enumerate(opts))
    prompt = f"{question}\n\nOptions:\n{labelled}" if opts else question
    checks: list[dict[str, Any]] = []
    if correct:
        checks.append({"type": "answer_contains", "text": correct})
    return Case(
        id=record_id,
        suite_id=suite_id,
        benchmark_family="gpqa",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"subdomain": row.get("Subdomain", ""), "correct_answer": correct},
        checks=checks,
    )


class GpqaSuite(Suite):
    id: str = "gpqa"
    name: str = "GPQA (diamond)"
    version: str = _CONFIG
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "Idavidrein/gpqa (gated, gpqa_diamond config); requires HUGGINGFACE_TOKEN "
        "in the vault. Skips cleanly when no token is provisioned."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def _token(self) -> str:
        return settings.huggingface_token

    def load_cases(self) -> list[Case]:
        # Hermetic fast-path: a committed slice (only present once fetched with a token).
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_row_to_case(r, self.id) for r in rows]

        token = self._token()
        if not token:
            logger.info("GPQA gated — set HUGGINGFACE_TOKEN in the vault to enable (suite skipped)")
            return []

        from datasets import load_dataset  # type: ignore[import-untyped]  # heavy, token-only

        ds = load_dataset(_DATASET, _CONFIG, token=token, split=_SPLIT)
        rows: list[dict[str, Any]] = [dict(r) for r in ds]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
        return [_row_to_case(r, self.id) for r in rows]
