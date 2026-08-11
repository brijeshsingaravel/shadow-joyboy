"""GAIA suite (token-gated).

GAIA (``gaia-benchmark/GAIA``) is a *gated* HF dataset: fetching it requires a free
``HUGGINGFACE_TOKEN`` (read from the master vault via ``settings``, never hardcoded).

Behaviour:
- **No token** → ``load_cases()`` returns ``[]`` and logs a clear message. The suite
  registers cleanly and simply contributes nothing until a token is provisioned.
- **Token present** → if a committed slice exists under ``gaia/data/`` it is used
  (hermetic); otherwise the level-1 validation split is fetched live and mapped.

Each task → a v2 ``Case`` with a ``multi_step_reasoning`` feature and an
``answer_contains`` check against the task's reference final answer.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent / "gaia" / "data"
_SLICE = DATA_DIR / "gaia_slice.json"

_FEATURES = ["multi_step_reasoning"]
_DATASET = "gaia-benchmark/GAIA"
_CONFIG = "2023_level1"
_SPLIT = "validation"


def _row_to_case(row: dict[str, Any], suite_id: str) -> Case:
    answer = str(row.get("Final answer", row.get("final_answer", ""))).strip()
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_contains", "text": answer})
    return Case(
        id=str(row.get("task_id", row.get("id", ""))),
        suite_id=suite_id,
        benchmark_family="gaia",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("Question", row.get("question", ""))),
        setup={"level": row.get("Level", row.get("level", ""))},
        checks=checks,
    )


class GaiaSuite(Suite):
    id: str = "gaia"
    name: str = "GAIA (2023 level-1 validation slice)"
    version: str = _CONFIG
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "gaia-benchmark/GAIA (gated); requires HUGGINGFACE_TOKEN in the vault. "
        "Skips cleanly when no token is provisioned."
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
            logger.info("GAIA gated — set HUGGINGFACE_TOKEN in the vault to enable (suite skipped)")
            return []

        from datasets import load_dataset  # type: ignore[reportMissingTypeStubs]

        ds: Any = load_dataset(_DATASET, _CONFIG, token=token, split=_SPLIT)
        return [_row_to_case(dict(r), self.id) for r in ds]
