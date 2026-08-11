"""LegalBench suite (public) — benchmark-design.md §12f, the Atticus (Legal, vs Harvey)
vertical gap.

LegalBench (``nguha/legalbench``) is an open, expert-curated suite of 162 legal-reasoning tasks
across statutes, cases, and contracts (Guha et al., NeurIPS 2023). No token required. This is a
**curated v1 slice focused on contract review** (Atticus's actual vertical, "contract-drafter"),
not all 162 tasks — expand the ``_CONFIGS`` list to widen coverage later.

Verified live (s42) against the two configs used here:
- ``contract_qa`` (80 test rows): a clause + a yes/no question about what it covers.
- ``contract_nli_confidentiality_of_agreement`` (82 test rows): a clause from an NDA + a fixed
  NLI question (does the clause address confidentiality of the agreement itself), no per-row
  question column — the task's question is constant across the config.

Each row -> a v2 ``Case`` with a ``legal_reasoning`` feature; the check is ``answer_contains``
against the gold Yes/No label (LegalBench's classification tasks are binary in this slice).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "legalbench" / "data"
_SLICE = DATA_DIR / "legalbench_slice.json"

_FEATURES = ["legal_reasoning"]
_DATASET = "nguha/legalbench"
_SPLIT = "test"

# (config, fixed task question or None if the row itself carries a "question" column)
_CONFIGS: tuple[tuple[str, str | None], ...] = (
    ("contract_qa", None),
    (
        "contract_nli_confidentiality_of_agreement",
        "Does this clause discuss the confidentiality of the agreement itself "
        "(rather than the confidential information it defines)?",
    ),
)


def _row_to_case(
    row: dict[str, Any], suite_id: str, config: str, fixed_question: str | None, index: int
) -> Case:
    question = fixed_question or str(row.get("question", "")).strip()
    text = str(row.get("text", "")).strip()
    answer = str(row.get("answer", "")).strip()
    prompt = f"Clause:\n{text}\n\nQuestion: {question}\nAnswer Yes or No."
    checks: list[dict[str, Any]] = []
    if answer:
        checks.append({"type": "answer_contains", "text": answer})
    row_id = row.get("index", index)
    return Case(
        id=f"{config}_{row_id}",
        suite_id=suite_id,
        benchmark_family="legalbench",
        features=list(_FEATURES),
        tools=[],
        prompt=prompt,
        setup={"config": config, "gold_answer": answer},
        checks=checks,
    )


class LegalBenchSuite(Suite):
    id: str = "legalbench"
    name: str = "LegalBench (contract-review slice)"
    version: str = "v1-curated"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "nguha/legalbench (public, MIT); curated slice: contract_qa + "
        "contract_nli_confidentiality_of_agreement. Expert-curated legal-reasoning tasks "
        "(Guha et al., NeurIPS 2023). Fills the Atticus (Legal, vs Harvey) vertical gap "
        "confirmed in benchmark-design.md §12f."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            questions_by_config = dict(_CONFIGS)
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [
                _row_to_case(r, self.id, r["_config"], questions_by_config.get(r["_config"]), i)
                for i, r in enumerate(rows)
            ]

        from datasets import load_dataset  # type: ignore[import-untyped]  # heavy import

        cases: list[Case] = []
        for config, fixed_question in _CONFIGS:
            ds = load_dataset(_DATASET, config, split=_SPLIT)
            rows: list[dict[str, Any]] = [dict(r) for r in ds]  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
            cases.extend(
                _row_to_case(r, self.id, config, fixed_question, i) for i, r in enumerate(rows)
            )
        return cases
