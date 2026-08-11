"""RoleBench suite (persona / role consistency).

RoleBench (ZenMoore/RoleBench, Apache-2.0) - the agent answers as a named character/role; the test
is whether it stays in that role's voice + knowledge. Judge-scored on persona consistency. A
representative set of role-specific files loads live via ``hf_hub_download``; a vendored slice is
the offline fallback. Persona-drift dimension; held-out gate set. W0-A2.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

DATA_DIR = Path(__file__).resolve().parent / "rolebench" / "data"
_SLICE = DATA_DIR / "rolebench_slice.json"
_FEATURES = ["multi_turn_consistency"]
_ROLES = (
    "Abraham Lincoln",
    "Alvy Singer",
    "Angel",
    "Caesar",
    "Sheldon Cooper",
    "Twilight Sparkle",
    "Jack Sparrow",
    "Gandalf",
)


def _case(row: dict[str, Any], suite_id: str) -> Case:
    role = str(row.get("role", "")).strip()
    q = str(row.get("question", "")).strip()
    rubric = (
        f"Answer in-character as {role}. PASS if the reply fits {role}'s voice, knowledge, "
        "and worldview. FAIL if it breaks character or contradicts the persona."
    )
    return Case(
        id=str(row.get("id")),
        suite_id=suite_id,
        benchmark_family="rolebench",
        features=list(_FEATURES),
        tools=[],
        prompt=f"You are {role}. {q}",
        rubric=rubric,
        setup={"role": role, "split": "heldout"},
    )


def _load_full() -> list[dict[str, Any]] | None:
    try:
        from huggingface_hub import hf_hub_download  # type: ignore[reportMissingTypeStubs]

        rows: list[dict[str, Any]] = []
        for role in _ROLES:
            try:
                p: str = hf_hub_download(
                    "ZenMoore/RoleBench",
                    f"instructions-eng/role-specific-{role}.jsonl",
                    repo_type="dataset",
                )
            except Exception:
                continue
            for j, line in enumerate(open(p, encoding="utf-8")):
                if not line.strip():
                    continue
                r = json.loads(line)
                q = r.get("question") or r.get("instruction") or next(iter(r.values()), "")
                rows.append({"id": f"role_{role[:3]}_{j}", "role": role, "question": str(q)})
        return rows or None
    except Exception:
        return None


class RoleBenchSuite(Suite):
    id: str = "rolebench"
    name: str = "RoleBench (persona / role consistency)"
    version: str = "eng"
    kind: Literal["external", "native", "dataset"] = "dataset"
    provenance: str = (
        "ZenMoore/RoleBench (Apache-2.0); in-character role-play, judge-scored on "
        "persona consistency (persona-drift axis). Held-out gate set."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        rows = _load_full()
        if rows is None and _SLICE.exists():
            rows = json.loads(_SLICE.read_text(encoding="utf-8"))
        return [_case(r, self.id) for r in (rows or [])]
