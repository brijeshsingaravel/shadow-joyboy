"""GDPval suite (real-world economic task value).

GDPval (openai/gdpval) — 220 real professional tasks across sectors/occupations, each with an
expert deliverable reference; the headline "is the agent actually useful vs a human expert"
benchmark. Slice vendored under ``gdpval/data/`` (20 tasks across sectors). Scoring is
**judge-based** (the runner's judge scores the produced deliverable against a per-task
``rubric``) — graded by the free judges (Groq gpt-oss-120b / gemini-flash), no exact-match.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "gdpval" / "data"
_SLICE = DATA_DIR / "gdpval_slice.json"
_FEATURES = ["real_world_deliverable", "domain_expertise"]


def _case(row: dict[str, Any], suite_id: str) -> Case:
    occ = row.get("occupation", "")
    sec = row.get("sector", "")
    rubric = (
        f"The response is a complete, professional-quality deliverable for a {occ} in the "
        f"{sec} sector that fully addresses the task: correct, well-structured, and directly "
        "usable — comparable in substance to an expert's work. Pass only if it would satisfy "
        "the requester; fail if partial, generic, or off-task."
    )
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="gdpval",
        features=list(_FEATURES),
        tools=[],
        prompt=str(row.get("prompt", "")).strip(),
        rubric=rubric,
        setup={"sector": sec, "occupation": occ},
    )


class GdpvalSuite(Suite):
    id: str = "gdpval"
    name: str = "GDPval (real-world economic task value)"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "openai/gdpval (public); real professional tasks vs expert deliverables "
        "— judge-scored against a per-task rubric."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_case(r, self.id) for r in rows]
        return []
