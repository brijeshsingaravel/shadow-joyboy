"""EQ-Bench Creative Writing suite (public) — benchmark-design.md §12f, the Andy
(Creative, vs Jasper/NoimosAI-adjacent) vertical gap + the Creative & Media capability gap.

``EQ-bench/creative-writing-bench`` (GitHub, public; no LICENSE file found on verification s42
— noted honestly, not asserted as MIT/Apache. Data-only usage, internal eval tooling per
benchmark-design.md §12f BD5, not redistributed) provides 32 real creative-writing prompts
across genres (historical fiction, horror, sci-fi, fantasy, romance, etc.), each with a
``<SEED>`` placeholder resolved to one of 10 optional detail modifiers.

WritingPreferenceBench (m-a-p/Writing-Preference-Bench) was considered but is a
preference-PAIR dataset built for judge/reward-model evaluation, not agent generation —
a worse fit than EQ-Bench's direct prompt+rubric shape, which mirrors every other Case in
this codebase.

Judge rubric is a condensed version of EQ-Bench's public judging dimensions (36 criteria in
the original; condensed to the load-bearing ones here, matching this codebase's existing
rubric-flattening pattern rather than modeling all 36 as separate checks).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "eqbench_creative" / "data"
_SLICE = DATA_DIR / "eqbench_creative_slice.json"

_FEATURES = ["creative_writing"]

_RUBRIC = (
    "Score 0-1 on EQ-Bench's core creative-writing dimensions: (a) faithful to the writing "
    "prompt's specific constraints (POV, tense, length, genre, the seed detail); (b) avoids "
    "weak/expository dialogue; (c) shows rather than tells emotion and character; (d) avoids "
    "purple prose and forced metaphor; (e) reads as a genuine slice of a larger story, not a "
    "self-contained vignette that resets to neutral by the end."
)


def _resolve_seed(prompt_text: str, seed_modifiers: list[str], key: str) -> str:
    if not seed_modifiers:
        return prompt_text.replace("<SEED>", "").strip()
    chosen = seed_modifiers[hash(key) % len(seed_modifiers)]
    return prompt_text.replace("<SEED>", chosen)


def _entry_to_case(key: str, entry: dict[str, Any], suite_id: str) -> Case:
    resolved = _resolve_seed(
        str(entry.get("writing_prompt", "")), list(entry.get("seed_modifiers", [])), key
    )
    return Case(
        id=f"eqbench_{key}",
        suite_id=suite_id,
        benchmark_family="eqbench_creative",
        features=list(_FEATURES),
        tools=[],
        prompt=resolved,
        setup={"category": entry.get("category", ""), "title": entry.get("title", "")},
        checks=[],
        rubric=_RUBRIC,
    )


class EqBenchCreativeSuite(Suite):
    id: str = "eqbench_creative"
    name: str = "EQ-Bench Creative Writing (v3 prompts)"
    version: str = "v3"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "EQ-bench/creative-writing-bench (public GitHub; no LICENSE file found on "
        "verification s42, noted honestly — data-only usage, internal eval tooling per "
        "benchmark-design.md §12f BD5). Fills the Andy (Creative, vs Jasper) vertical gap "
        "and the Creative & Media capability gap."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        if _SLICE.exists():
            data = json.loads(_SLICE.read_text(encoding="utf-8"))
            return [_entry_to_case(k, v, self.id) for k, v in data.items()]

        import httpx

        url = (
            "https://raw.githubusercontent.com/EQ-bench/creative-writing-bench/"
            "main/data/creative_writing_prompts_v3.json"
        )
        resp = httpx.get(url, timeout=30.0)
        resp.raise_for_status()
        data = resp.json()
        return [_entry_to_case(k, v, self.id) for k, v in data.items()]
