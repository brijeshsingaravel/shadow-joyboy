"""AppWorld external suite adapter (isolated subprocess).

AppWorld (StonyBrookNLP/appworld) is an interactive app-control agent benchmark:
the agent solves day-to-day tasks by calling ~9 apps' Python APIs against a live
local environment, scored by the AppWorld evaluator (Task / Scenario Goal
Completion). Its deps + encrypted task code conflict with the main env, so it
installs into its own venv under ``.benchmarks/appworld`` (the pip package +
``appworld install``/``download``) plus a clone of the repo under
``.benchmarks/appworld-repo`` (the baseline agents + experiment configs that
``appworld run`` needs). This adapter is driven via subprocess — never imported.

Routing: AppWorld's ``openai`` client uses the OpenAI SDK, which honours
``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``. We point those at our LiteLLM proxy and
write a routed experiment config whose ``model.name`` is the proxy model id, so
``appworld run`` drives its OWN baseline agent on our model (the tau2 pattern).
The key is injected via the subprocess env only — never logged or persisted.

The live run is heavy (a multi-step code-gen agent over the full split); like the
SWE-bench/τ²-bench adapters, the hermetic test mocks the subprocess + a captured
evaluation shape, and a ``@pytest.mark.live`` test runs it for real.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_APPWORLD_DIR = _PROJECT_ROOT / ".benchmarks" / "appworld"
_APPWORLD_REPO = _PROJECT_ROOT / ".benchmarks" / "appworld-repo"
_TEMPLATE_CONFIG = (
    _APPWORLD_REPO
    / "experiments"
    / "configs"
    / "simplified_function_calling_agent"
    / "openai"
    / "gpt-4o-mini-2024-07-18"
    / "test_normal.jsonnet"
)

_FEATURES = ["tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["code"]
# Wall-clock cap (s) so a wedged agent run can't block a sweep forever.
_TIMEOUT_SECS = float(os.environ.get("MADRAS_APPWORLD_TIMEOUT", "7200"))


def _python_exe() -> Path:
    win = _APPWORLD_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _APPWORLD_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _appworld_exe() -> Path:
    win = _APPWORLD_DIR / ".venv" / "Scripts" / "appworld.exe"
    posix = _APPWORLD_DIR / ".venv" / "bin" / "appworld"
    return win if win.exists() else posix


def _subprocess_env() -> dict[str, str]:
    """Env that points AppWorld's OpenAI client at our LiteLLM proxy.

    ``litellm_base_url`` already carries the ``/v1`` suffix the OpenAI client
    expects. PYTHONUTF8 avoids AppWorld's cp1252 crash on its emoji output.
    """
    env = dict(os.environ)
    env["OPENAI_BASE_URL"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _write_routed_config(model: str, dataset: str, experiment_name: str) -> None:
    """Copy the gpt-4o-mini template, repoint its model name + dataset to ours.

    The OpenAI-compatible proxy routes by the model id, so ``name = <model>``
    plus the OPENAI_BASE_URL env is all the routing needed. Written to
    ``experiments/configs/<experiment_name>.jsonnet`` (the path ``appworld run``
    resolves an experiment from).
    """
    text = _TEMPLATE_CONFIG.read_text(encoding="utf-8")
    text = re.sub(r'("name":\s*)"[^"]+"', rf'\1"{model}"', text, count=1)
    text = re.sub(r'("dataset":\s*)"[^"]+"', rf'\1"{dataset}"', text, count=1)
    out = _APPWORLD_REPO / "experiments" / "configs" / f"{experiment_name}.jsonnet"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")


def parse_evaluation(evaluation: dict[str, Any], dataset: str, k: int) -> list[dict[str, Any]]:
    """Normalize an AppWorld ``evaluate_dataset`` dict into pg rows.

    Every key except ``aggregate`` is a task id mapping to that task's metrics;
    a task passes when its Task Goal Completion is satisfied (``task_goal_completion``
    truthy / 1.0, falling back to ``success`` or ``num_tests == num_passed``).
    """
    rows: list[dict[str, Any]] = []
    for task_id, raw in evaluation.items():
        if task_id == "aggregate" or not isinstance(raw, dict):
            continue
        metrics = cast("dict[str, Any]", raw)
        passed = _task_passed(metrics)
        rows.append(
            {
                "scenario_id": f"appworld-{task_id}",
                "suite_id": "appworld",
                "benchmark_family": "appworld",
                "features": list(_FEATURES),
                "k": max(1, k),
                "passes": max(1, k) if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


def _task_passed(metrics: dict[str, Any]) -> bool:
    tgc = metrics.get("task_goal_completion")
    if isinstance(tgc, bool):
        return tgc
    if isinstance(tgc, (int, float)):
        return abs(float(tgc) - 1.0) <= 1e-6
    if isinstance(metrics.get("success"), bool):
        return bool(metrics["success"])
    total = metrics.get("num_tests")
    passed = metrics.get("num_passed_tests", metrics.get("num_passed"))
    if isinstance(total, int) and isinstance(passed, int) and total > 0:
        return passed >= total
    return False


class AppWorldSuite(Suite):
    """External self-running AppWorld suite (interactive app-control agent)."""

    id: str = "appworld"
    name: str = "AppWorld"
    version: str = "0.1.3"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "StonyBrookNLP/appworld; baseline simplified_function_calling agent driven "
        "on our model via the LiteLLM proxy. Heavy — full split is a long live run."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    dataset: str = "dev"

    def load_cases(self) -> list[Case]:
        """One coverage stub (external suite scores itself in its own harness)."""
        return [
            Case(
                id="appworld",
                suite_id=self.id,
                benchmark_family="appworld",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=f"AppWorld {self.dataset} split (external self-running suite)",
            )
        ]

    def _evaluate(self, experiment_name: str) -> dict[str, Any]:
        """Score the run via AppWorld's evaluator in the isolated venv."""
        code = (
            "import json, appworld;"
            f"appworld.update_root({str(_APPWORLD_REPO)!r});"
            f"d=appworld.evaluate_dataset({experiment_name!r},{self.dataset!r},"
            "print_report=False);print('__EVAL__'+json.dumps(d))"
        )
        proc = subprocess.run(
            [str(_python_exe()), "-c", code],
            cwd=str(_APPWORLD_REPO),
            env=_subprocess_env(),
            check=True,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
        )
        _, sep, payload = proc.stdout.partition("__EVAL__")
        if not sep:
            raise RuntimeError("AppWorld evaluate produced no report; check the run output.")
        return json.loads(payload)

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run the baseline agent on ``model`` over the split, then score it."""
        exe = _appworld_exe()
        if not exe.exists():
            raise FileNotFoundError(
                f"AppWorld not installed at {exe}. Run pip install appworld + "
                "`appworld install` + `appworld download data` in .benchmarks/appworld, "
                "and clone StonyBrookNLP/appworld to .benchmarks/appworld-repo."
            )
        eff_k = k if k is not None else 1
        experiment_name = f"madras_appworld_{model.replace('/', '_')}"
        _write_routed_config(model, self.dataset, experiment_name)
        try:
            subprocess.run(
                [str(exe), "run", experiment_name, "--root", str(_APPWORLD_REPO)],
                cwd=str(_APPWORLD_REPO),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"AppWorld run timed out after {_TIMEOUT_SECS:.0f}s "
                "(raise MADRAS_APPWORLD_TIMEOUT for the full split)."
            ) from exc
        evaluation = self._evaluate(experiment_name)
        return parse_evaluation(evaluation, self.dataset, eff_k)
