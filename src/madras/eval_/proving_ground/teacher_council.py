"""T4.1b -- the Distilabel Teacher-Council producer (sibling to dataset_compiler.py's
Synthetic-Data-Kit producer, 1a). Multi-teacher generation over dev-split Proving Ground cases,
scored via the same primitives ``sweep.py::run_case()`` composes internally
(``run_scenario`` -> ``score_deterministic`` -> ``judge_panel``), best-of-N winner kept as one
SFT row -- G3/G4-compliant, feeding the same ``pg_sft_rows`` sink as 1a.

Distilabel is the orchestration layer (concurrency/retry/multiprocess execution via its own
``Step``/``Pipeline`` model, confirmed: each Step runs in its own subprocess, so ``asyncio.run()``
inside ``process()`` never conflicts with an already-running event loop). Madras's own
``score_deterministic``/``judge_panel`` scoring is NOT replaced -- Distilabel's generic AI-judges
would lose the Proving-Ground-tuned rubric/deterministic discipline that's the differentiated IP
here (D41). ``run_scenario`` is a full agentic loop (tools, multi-step reasoning), not a single
chat completion, so it does not fit Distilabel's ``AsyncLLM.agenerate()`` contract (single-turn
text generation) -- custom ``Step`` subclasses that bridge into the async engine directly are the
right shape, not ``AsyncLLM``/``Task``.

G2 (D41): every teacher is vetted per-model (license + provider ToS) before its output can enter
a training set -- ``TEACHER_ALLOWLIST`` ships fail-closed. G4: only dev-split cases are read
(``heldout.dev_cases()``), never the held-out firewall.

Deliberately NO ``from __future__ import annotations`` in this file (unlike the rest of the
codebase): Distilabel's own ``Step`` validation (``is_parameter_annotated_with``, `typing_.py`)
inspects ``process()``'s parameter annotation via a raw ``inspect.signature()`` without resolving
PEP 563 postponed (stringified) annotations -- with the future import, ``process``'s ``inputs``
parameter would show up as the literal string ``'StepInput'`` instead of the real ``Annotated``
object, and Distilabel's pipeline-build-time validation would reject every Step here with
"should have a parameter with type hint StepInput" even though the code is correct. Safe on
Python 3.11 (this project's pin): builtin generics (``dict[str, Any]``, ``list[str]``) are real
runtime objects here, not just typing-only syntax that needs the future import.
"""

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any

from distilabel.pipeline import Pipeline  # type: ignore[reportMissingTypeStubs]
from distilabel.steps import (  # type: ignore[reportMissingTypeStubs]
    GlobalStep,
    LoadDataFromDicts,
    Step,
    StepInput,
    StepOutput,
)

from madras.eval_.proving_ground.judge_panel import JUDGES_DEFAULT, PanelVerdict, judge_panel
from madras.eval_.proving_ground.judge_runner import make_judge_call
from madras.eval_.proving_ground.runner import run_scenario
from madras.eval_.proving_ground.scoring import DetResult, score_deterministic
from madras.eval_.proving_ground.suite import Case
from madras.eval_.proving_ground.suites import SUITES

# Reused, not duplicated: the exact Case->Scenario mapping run_case() relies on internally.
from madras.eval_.proving_ground.sweep import case_to_scenario

PRODUCER_TEACHER_COUNCIL = "distilabel-teacher-council"


@dataclass(frozen=True)
class TeacherLicense:
    cleared: bool
    license: str
    notes: str


# G2 due diligence (preliminary research, s49 -- not legal sign-off). Fails closed: a model
# absent from this dict, or present with cleared=False, never enters the Teacher Council.
TEACHER_ALLOWLIST: dict[str, TeacherLicense] = {
    "deepseek-r1": TeacherLicense(
        cleared=True,
        license="MIT",
        notes="DeepSeek's own paper + license explicitly encourage distillation.",
    ),
    "qwen3": TeacherLicense(
        cleared=True,
        license="Apache-2.0",
        notes="No training-restriction clause found.",
    ),
    "llama-70b": TeacherLicense(
        cleared=False,
        license="Llama 3 Community License",
        notes="Historically contentious 'don't use outputs to improve other LLMs' clause "
        "depending on version -- not cleared without real per-version ToS confirmation.",
    ),
    "gemini-flash": TeacherLicense(
        cleared=False,
        license="Google API ToS (closed)",
        notes="Closed frontier APIs typically prohibit training a competing model on outputs "
        "-- not cleared by default.",
    ),
}


def cleared_teachers() -> list[str]:
    """Sorted, deterministic list of G2-cleared teacher model ids."""
    return sorted(model for model, lic in TEACHER_ALLOWLIST.items() if lic.cleared)


def candidate_composite_score(det: DetResult, verdict: PanelVerdict) -> float:
    """Blend the two Proving Ground scoring dimensions -- deterministic checks and the judge
    panel -- into one comparable score for best-of-N selection. Mirrors how ``run_case`` treats
    det/judge as the two real signals (no new scoring philosophy invented here)."""
    det_score = 1.0 if det.passed else 0.0
    judge_score = (verdict.n_pass / len(verdict.votes)) if verdict.votes else 0.0
    return (det_score + judge_score) / 2.0


def select_winners(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Best-of-N v1: group teacher candidates by ``case_id``, keep the single best-scoring one
    per case. Full critique-and-merge consensus is a documented future enhancement, not built
    here (YAGNI for a first pass)."""
    best_by_case: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        case_id = candidate["case_id"]
        current_best = best_by_case.get(case_id)
        if current_best is None or candidate["composite_score"] > current_best["composite_score"]:
            best_by_case[case_id] = candidate
    return list(best_by_case.values())


def shape_sft_row(
    case: Case,
    winner: dict[str, Any],
    *,
    tenant: str,
    consent: bool,
    mining_run_id: str,
) -> dict[str, Any]:
    """Shape one best-of-N winner into a ``pg_sft_rows``-ready dict -- the same shape
    ``dataset_compiler.py::synth_kit_producer`` produces for 1a, so both producers write through
    the same ``store_v2.write_sft_rows()`` sink. G3: tenant/consent/provenance on every row."""
    row_key = hashlib.sha256(
        f"{PRODUCER_TEACHER_COUNCIL}|{mining_run_id}|{case.id}".encode()
    ).hexdigest()[:16]
    return {
        "id": f"sft-{row_key}",
        "tenant": tenant,
        "consent": consent,
        "producer": PRODUCER_TEACHER_COUNCIL,
        "source_id": case.id,
        "prompt": case.prompt,
        "completion": str(winner["trajectory"].get("answer", "")),
        "score": winner["composite_score"],
        "provenance": {
            "teacher": winner["teacher_model"],
            "mining_run_id": mining_run_id,
            "suite_id": winner["suite_id"],
            "producer": PRODUCER_TEACHER_COUNCIL,
        },
    }


def _case_by_id(suite_id: str, case_id: str) -> Case:
    suite = SUITES[suite_id]
    for case in suite.load_cases():
        if case.id == case_id:
            return case
    raise KeyError(f"case {case_id!r} not found in suite {suite_id!r}")


async def _generate_candidate(case: Case, model: str, *, gateway_for: Any) -> dict[str, Any]:
    """One (case, teacher) completion via the SAME agentic loop entry point run_case() uses
    internally -- run_scenario, not a bare chat completion (a teacher's tool-using trajectory is
    the thing being distilled, not just its final text)."""
    scenario = case_to_scenario(case)
    run = await run_scenario(scenario, gateway=gateway_for(model), k=1, model=model)
    empty: dict[str, Any] = {"answer": "", "tools": [], "refused": False}
    traj: dict[str, Any] = next(
        (t for t in reversed(run.trajectories) if not t.get("error")),
        run.trajectories[-1] if run.trajectories else empty,
    )
    return {
        "case_id": case.id,
        "suite_id": case.suite_id,
        "teacher_model": model,
        "trajectory": traj,
    }


async def _generate_all_candidates(
    case: Case,
    teachers: list[str],
    *,
    gateway_for: Any,
    generate_fn: Any = _generate_candidate,
) -> list[dict[str, Any]]:
    """Runs every teacher concurrently for one case; one teacher's failure (rate limit,
    misconfigured route, transient error -- all real, expected operational conditions) never
    sinks the whole case, same posture judge_panel already takes for one bad judge. A case where
    every teacher fails simply contributes no candidate (still visible via a shorter result, not
    a crash)."""
    results = await asyncio.gather(
        *(generate_fn(case, m, gateway_for=gateway_for) for m in teachers),
        return_exceptions=True,
    )
    return [r for r in results if not isinstance(r, BaseException)]


async def _score_one(
    candidate: dict[str, Any], case: Case, *, judges: list[str], judge_call: Any
) -> dict[str, Any]:
    scenario = case_to_scenario(case)
    det = score_deterministic(scenario, candidate["trajectory"])
    verdict = await judge_panel(
        case.rubric, case.prompt, candidate["trajectory"], judges=judges, call=judge_call
    )
    return {**candidate, "composite_score": candidate_composite_score(det, verdict)}


class TeacherCouncilGenerate(Step):
    """Expands each ``{suite_id, case_id}`` input row into one output row per G2-cleared teacher
    model, running the real agentic loop concurrently across teachers for that case. Runs in its
    own subprocess (Distilabel's execution model) -- ``asyncio.run()`` here never conflicts with
    an already-running event loop elsewhere."""

    @property
    def inputs(self) -> list[str]:
        return ["suite_id", "case_id"]

    @property
    def outputs(self) -> list[str]:
        return ["case_id", "suite_id", "teacher_model", "trajectory"]

    def load(self) -> None:
        super().load()
        from madras.config import settings
        from madras.llm.gateway import LLMGateway
        from madras.llm.litellm import LiteLLMBackend

        def _gateway_for(model: str) -> LLMGateway:
            return LLMGateway(
                backend=LiteLLMBackend(
                    api_key=settings.litellm_master_key, base_url=settings.litellm_base_url
                )
            )

        self._gateway_for = _gateway_for

    def process(self, inputs: StepInput) -> StepOutput:  # type: ignore[reportIncompatibleMethodOverride]
        out: list[dict[str, Any]] = []
        for row in inputs:
            case = _case_by_id(row["suite_id"], row["case_id"])
            candidates = asyncio.run(
                _generate_all_candidates(case, cleared_teachers(), gateway_for=self._gateway_for)
            )
            out.extend(candidates)
        yield out


class ProvingGroundScore(Step):
    """Scores each teacher candidate via ``score_deterministic`` + ``judge_panel`` -- the exact
    primitives ``run_case`` composes internally. Never Distilabel's own generic AI-judges (the
    Proving-Ground-tuned rubric/deterministic discipline is the differentiated IP, D41)."""

    @property
    def inputs(self) -> list[str]:
        return ["case_id", "suite_id", "teacher_model", "trajectory"]

    @property
    def outputs(self) -> list[str]:
        return ["case_id", "suite_id", "teacher_model", "trajectory", "composite_score"]

    def load(self) -> None:
        super().load()
        from madras.config import settings
        from madras.llm.gateway import LLMGateway
        from madras.llm.litellm import LiteLLMBackend

        def _gateway_for(model: str) -> LLMGateway:
            return LLMGateway(
                backend=LiteLLMBackend(
                    api_key=settings.litellm_master_key, base_url=settings.litellm_base_url
                )
            )

        self._judges = list(JUDGES_DEFAULT)
        self._judge_call = make_judge_call(_gateway_for)

    def process(self, inputs: StepInput) -> StepOutput:  # type: ignore[reportIncompatibleMethodOverride]
        out: list[dict[str, Any]] = []
        for row in inputs:
            case = _case_by_id(row["suite_id"], row["case_id"])

            async def _score(row: dict[str, Any] = row, case: Case = case) -> dict[str, Any]:
                return await _score_one(row, case, judges=self._judges, judge_call=self._judge_call)

            out.append(asyncio.run(_score()))
        yield out


class SelectWinner(GlobalStep):
    """Best-of-N selection -- needs ALL scored candidates for a case before it can pick the
    winner, hence a GlobalStep (waits for its upstream to fully finish, per Distilabel's own
    execution-stage model)."""

    @property
    def inputs(self) -> list[str]:
        return ["case_id", "suite_id", "teacher_model", "trajectory", "composite_score"]

    @property
    def outputs(self) -> list[str]:
        return ["case_id", "suite_id", "teacher_model", "trajectory", "composite_score"]

    def process(self, inputs: StepInput) -> StepOutput:  # type: ignore[reportIncompatibleMethodOverride]
        yield select_winners(inputs)


def build_pipeline(cases: list[Case]) -> Pipeline:
    """Wires the dev-split case source -> parallel per-teacher generation -> scoring ->
    best-of-N selection, per the signed-off design. Real subprocess execution is exercised in
    live verification, not hermetic unit tests (each Step's own logic is unit-tested directly)."""
    rows = [{"suite_id": c.suite_id, "case_id": c.id} for c in cases]
    with Pipeline(name="madras-teacher-council") as pipeline:
        source = LoadDataFromDicts(name="dev_cases", data=rows)
        generate = TeacherCouncilGenerate(name="generate")
        score = ProvingGroundScore(name="score")
        select = SelectWinner(name="select_winner")
        _ = source >> generate >> score >> select
    return pipeline


async def run_teacher_council(
    cases: list[Case],
    *,
    tenant: str = "default",
    consent: bool = True,
    mining_run_id: str,
) -> list[dict[str, Any]]:
    """Founder-invoked entry point: runs the full pipeline over ``cases`` (already filtered to
    dev-split by the caller, per G4 -- ``heldout.dev_cases()``) and shapes the winners into
    ``pg_sft_rows``-ready dicts."""
    if not cases:
        return []
    by_id = {c.id: c for c in cases}
    pipeline = build_pipeline(cases)
    # Real bug (live-verified): pipeline.run() registers its own SIGINT handler, which Python
    # only allows from the main thread of the main interpreter -- asyncio.to_thread() runs it in
    # a worker thread and raises ValueError. Call it directly (blocking); this is a single-shot
    # CLI entry point with nothing else running concurrently on the event loop, so blocking here
    # costs nothing real.
    distiset: Any = pipeline.run(use_cache=False)  # type: ignore[reportUnknownMemberType]
    winners: list[dict[str, Any]] = list(distiset["default"]["train"])
    return [
        shape_sft_row(
            by_id[w["case_id"]], w, tenant=tenant, consent=consent, mining_run_id=mining_run_id
        )
        for w in winners
    ]
