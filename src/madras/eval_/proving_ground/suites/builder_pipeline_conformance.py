"""Builder-pipeline conformance — closes the confirmed 0-suite Agent Builder gap from §12d.

benchmark-design.md §12d found: **no suite tests the Builder UX/compile-to-residency pipeline
itself as a product** (distinct from the compiled agent it produces, which IS covered by
`compile_conformance` + the agent-vertical suites). This suite targets the pipeline's own
integrity logic — deterministic, zero-LLM, against `compile.py::target_role_path`'s real
name-collision handling (a hand-authored role name is protected; a compiled-role collision
auto-suffixes; no collision uses the requested name as-is).

Grounded in a real s38 finding (Knowledge/Ideas.md): the auto-suffix-on-collision logic was
itself a live-drive-caught bugfix — exactly the kind of pipeline-integrity property that
should have permanent regression coverage, not just a one-time manual fix. A full FakeBackend-
driven `compile_agent()` end-to-end case was considered but dropped — it needs a real
`base_agent.yaml`/neighborhood config tree plus `Catalog`/`AuthContext` fixtures, too heavy/
fragile for a conformance suite; logged as a follow-on, not built here.

Extended s42 (§12l, Builder audit) with the compile->simulate->verify closed loop's other
pure seam: `compiler/verify.py`'s scenario-selection filter (toolset-subset gating) and its
fail-closed guarantee (a capability-starved agent — zero eligible scenarios — must never
vacuously pass). Both are zero-LLM: the fail-closed case is proven by passing gateway=None
and reaching a clean result, which is only possible if the LLM-calling loop body genuinely
never executed.
"""

from __future__ import annotations

import tempfile
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["builder_pipeline"]


def _case_collision_with_hand_authored_role_raises() -> tuple[bool, str]:
    from madras.compiler.compile import RoleNameCollision, target_role_path

    with tempfile.TemporaryDirectory() as tmp:
        agents_dir = Path(tmp)
        (agents_dir / "roles").mkdir(parents=True)
        (agents_dir / "roles" / "shadow.yaml").write_text("name: shadow\n", encoding="utf-8")
        try:
            target_role_path(agents_dir, "shadow")
            return False, "expected RoleNameCollision, none raised"
        except RoleNameCollision:
            return True, "correctly raised on hand-authored role collision"


def _case_collision_with_compiled_role_auto_suffixes() -> tuple[bool, str]:
    from madras.compiler.compile import target_role_path

    with tempfile.TemporaryDirectory() as tmp:
        agents_dir = Path(tmp)
        (agents_dir / "compiled").mkdir(parents=True)
        (agents_dir / "compiled" / "triage_scribe.yaml").write_text("name: x\n", encoding="utf-8")
        path, name = target_role_path(agents_dir, "triage_scribe")
        ok = name == "triage_scribe_2" and path.name == "triage_scribe_2.yaml"
        return ok, f"name={name!r} path={path.name!r}"


def _case_no_collision_uses_the_requested_name() -> tuple[bool, str]:
    from madras.compiler.compile import target_role_path

    with tempfile.TemporaryDirectory() as tmp:
        agents_dir = Path(tmp)
        path, name = target_role_path(agents_dir, "brand_new_agent")
        ok = name == "brand_new_agent" and path.name == "brand_new_agent.yaml"
        return ok, f"name={name!r}"


def _case_verify_scenario_selection_filters_by_toolset_subset() -> tuple[bool, str]:
    from madras.compiler.verify import select_light_scenarios
    from madras.eval_.proving_ground.scenario import Scenario

    def _s(sid: str, tools: list[str]) -> Scenario:
        return Scenario(
            id=sid,
            benchmark_family="madras",
            features=[],
            topic="t",
            task="t",
            setup={"tools": tools},
            checks=[],
            rubric="r",
            difficulty="med",
            k=1,
        )

    scenarios = [
        _s("needs_email", ["email"]),
        _s("needs_nothing", []),
        _s("needs_both", ["email", "calendar"]),
    ]
    # An agent with only "email" can attempt scenarios whose tool requirement is a SUBSET
    # of what it has -- "needs_both" requires calendar too, which this agent lacks.
    selected = select_light_scenarios(scenarios, agent_toolsets=["email"], limit=5)
    ids = {s.id for s in selected}
    ok = ids == {"needs_email", "needs_nothing"}
    return ok, f"selected={sorted(ids)}"


def _case_verify_capability_starved_agent_fails_closed_no_llm_call() -> tuple[bool, str]:
    import asyncio
    from unittest.mock import patch

    from madras.compiler.verify import verify_agent
    from madras.factory.spawn import AgentRecord
    from madras.models.agent_config import AgentConfig, Origin, Rank

    config = AgentConfig(
        schema_version="1",
        constitution_version="1",
        name="capability_starved_agent",
        archetype="the_intern",
        neighborhood="tidel_park",
        origin=Origin.NATIVE,
        rank=Rank.INTERN,
        toolsets=["some_toolset_no_scenario_declares"],
    )
    record = AgentRecord(config=config)
    # Force zero eligible scenarios (the real capability-starved case the module's own
    # docstring calls out) rather than relying on the live scenario bank's exact contents
    # (which includes some zero-tool scenarios eligible for any agent). gateway=None proves
    # the LLM-calling loop body genuinely never runs when scores stays empty -- it would
    # crash on the first real call otherwise.
    with patch("madras.compiler.verify.load_scenarios", return_value=[]):
        result = asyncio.run(verify_agent(record, gateway=None, model="irrelevant"))  # type: ignore[arg-type]
    ok = result.passed is False and result.index == 0.0 and result.per_gate == {}
    return ok, f"passed={result.passed} index={result.index} per_gate={result.per_gate}"


_EXECUTORS: dict[str, Any] = {
    "collision_with_hand_authored_role_raises": _case_collision_with_hand_authored_role_raises,
    "collision_with_compiled_role_auto_suffixes": _case_collision_with_compiled_role_auto_suffixes,
    "no_collision_uses_the_requested_name": _case_no_collision_uses_the_requested_name,
    "verify_scenario_selection_filters_by_toolset_subset": (
        _case_verify_scenario_selection_filters_by_toolset_subset
    ),
    "verify_capability_starved_agent_fails_closed_no_llm_call": (
        _case_verify_capability_starved_agent_fails_closed_no_llm_call
    ),
}


def _run_case(case_id: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        passed, detail = _EXECUTORS[case_id]()
    except Exception as exc:
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": case_id,
        "suite_id": "builder_pipeline_conformance",
        "benchmark_family": "builder_pipeline_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "pipeline_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class BuilderPipelineConformanceSuite(Suite):
    id: str = "builder_pipeline_conformance"
    name: str = "Builder-pipeline conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original — 3 deterministic (zero-LLM) role-naming-collision cases against the "
        "REAL compiler/compile.py::target_role_path. Fills the Agent Builder pipeline gap "
        "confirmed in benchmark-design.md §12d/§12g — no prior suite tested the Builder pipeline "
        "itself, only the agent it produces. A full compile_agent() end-to-end case was "
        "considered but dropped (needs heavier fixtures); logged as a follow-on."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"builder_pipeline_conformance-{case_id}",
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
        del model, k, concurrency
        return [_run_case(case_id) for case_id in _EXECUTORS]
