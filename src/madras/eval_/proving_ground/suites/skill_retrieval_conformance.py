"""Skill-retrieval conformance — the deterministic conformance suite for the Skills subsystem.

benchmark-design.md §12f identified a gap: no suite tests whether skill-retrieval picks the
RIGHT skill for a task (distinct from BD9's skill-*growth* scoring, which measures whether the
GEPA training loop improves a skill over time — this is about retrieval *accuracy* at use-time).

``skills/retrieval.py::retrieve_skills`` is a pure deterministic word-token-overlap matcher —
no LLM call, no network — so this is a zero-LLM conformance suite exactly like C1-C5
(identity_boundary/routing_resilience/durable_state/compile/memory_sovereignty), driving the
REAL retrieval function against fixture skill sets, not a re-implementation of the matching
logic.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite
from madras.skills.format import Skill
from madras.skills.retrieval import retrieve_skills

_FEATURES = ["skill_retrieval_accuracy"]


@dataclass
class _FakeStore:
    """Minimal in-memory store matching the real SkillStore's list_active/search_active shape."""

    project_skills: dict[str, list[Skill]] = field(default_factory=dict[str, list[Skill]])
    library_skills: list[Skill] = field(default_factory=list[Skill])
    raise_on_list: bool = False

    async def list_active(self, *, project: str) -> list[Skill]:
        if self.raise_on_list:
            raise RuntimeError("simulated store outage")
        return self.project_skills.get(project, [])

    async def search_active(self, *, project: str, terms: list[str], limit: int) -> list[Skill]:
        del project, limit
        stripped = {t.strip("%").lower() for t in terms}
        return [
            s
            for s in self.library_skills
            if stripped & {w for w in (s.name + " " + s.description).lower().split()}
        ]


def _skill(name: str, description: str) -> Skill:
    return Skill(name=name, description=description, body=f"body for {name}")


_HIRING = _skill("hiring-scorecard", "Build a structured hiring scorecard for a role")
_CAMPAIGN = _skill("campaign-plan", "Draft a marketing campaign plan and budget")
_CONTRACT = _skill("contract-review", "Review a legal contract clause for risk")


def _case_right_skill_surfaces() -> tuple[bool, str]:
    store = _FakeStore(project_skills={"p": [_HIRING, _CAMPAIGN, _CONTRACT]})
    result = asyncio.run(
        retrieve_skills(store, project="p", user_input="how do I write a hiring scorecard")
    )
    ok = "hiring-scorecard" in result.matched_names and len(result.matched_names) == 1
    return ok, f"matched={result.matched_names}"


def _case_max_full_cap_respected() -> tuple[bool, str]:
    skills = [_skill(f"scorecard-{i}", "hiring scorecard template") for i in range(5)]
    store = _FakeStore(project_skills={"p": skills})
    result = asyncio.run(
        retrieve_skills(
            store,
            project="p",
            user_input="hiring scorecard template",
            max_full=2,
            library_project=None,
        )
    )
    ok = len(result.matched_names) == 2 and len(result.l0_lines) == 5
    return ok, f"matched={len(result.matched_names)} l0={len(result.l0_lines)}"


def _case_zero_overlap_returns_no_matches() -> tuple[bool, str]:
    store = _FakeStore(project_skills={"p": [_HIRING, _CAMPAIGN, _CONTRACT]})
    result = asyncio.run(
        retrieve_skills(store, project="p", user_input="xyzzy quux plugh", library_project=None)
    )
    ok = result.matched_names == [] and len(result.l0_lines) == 3
    return ok, f"matched={result.matched_names}"


def _case_library_fallback_search() -> tuple[bool, str]:
    library_skill = _skill("expense-audit", "Audit an expense report for policy violations")
    store = _FakeStore(project_skills={"p": []}, library_skills=[library_skill])
    result = asyncio.run(retrieve_skills(store, project="p", user_input="audit an expense report"))
    ok = "expense-audit" in result.matched_names and any(
        "(library)" in body for body in result.full_bodies
    )
    return ok, f"matched={result.matched_names}"


def _case_store_exception_returns_empty() -> tuple[bool, str]:
    store = _FakeStore(raise_on_list=True)
    result = asyncio.run(
        retrieve_skills(store, project="p", user_input="anything at all", library_project=None)
    )
    ok = result.l0_lines == [] and result.matched_names == []
    return ok, "store outage handled without raising"


_EXECUTORS: dict[str, Any] = {
    "right_skill_surfaces_among_decoys": _case_right_skill_surfaces,
    "max_full_cap_respected": _case_max_full_cap_respected,
    "zero_overlap_returns_no_matches": _case_zero_overlap_returns_no_matches,
    "library_fallback_search": _case_library_fallback_search,
    "store_exception_returns_empty": _case_store_exception_returns_empty,
}


def _run_case(case_id: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        passed, detail = _EXECUTORS[case_id]()
    except Exception as exc:  # a raising executor is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": case_id,
        "suite_id": "skill_retrieval_conformance",
        "benchmark_family": "skill_retrieval_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "retrieval_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class SkillRetrievalConformanceSuite(Suite):
    id: str = "skill_retrieval_conformance"
    name: str = "Skill-retrieval conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the real "
        "skills/retrieval.py::retrieve_skills against fixture skill sets. Fills the Skills "
        "subsystem gap confirmed in benchmark-design.md §12f."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"skill_retrieval_conformance-{case_id}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(_FEATURES),
                tools=[],
                prompt=f"[conformance] {case_id}",
                setup={},
                checks=[],
            )
            for case_id in _EXECUTORS
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency  # deterministic + zero-cost: irrelevant, no LLM call at all
        return [_run_case(case_id) for case_id in _EXECUTORS]
