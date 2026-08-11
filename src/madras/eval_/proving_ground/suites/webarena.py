"""WebArena external suite adapter (isolated subprocess; infra-gated).

WebArena (web-arena-x/webarena, run via ServiceNow's BrowserGym) tests a web
agent on realistic multi-site tasks (shopping / reddit / gitlab / wikipedia /
map). It needs (1) the hosted WebArena **sites** — a multi-GB Docker stack the
operator provisions, addressed via the ``WA_*`` env vars — and (2) a Playwright
browser. Its deps conflict with the main env, so it installs into its own venv
under ``.benchmarks/webarena`` and is driven via subprocess — never imported.

Routing: the runner (``scripts/webarena_runner.py``, executed in the isolated
venv) drives a minimal accessibility-tree agent whose LLM calls go to our
LiteLLM proxy (``OPENAI_BASE_URL`` / ``OPENAI_API_KEY``), and writes per-task
rewards as JSON. WebArena reward is 0/1, so a task passes on reward 1.0.

Live-gated: ``run`` raises a clear error unless the ``WA_*`` site env vars + the
isolated venv are present (the operator must stand up the Docker sites first).
Like the SWE-bench adapter, the hermetic test pins the parse; a real run is a
separate, infra-dependent live smoke.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_WEBARENA_DIR = _PROJECT_ROOT / ".benchmarks" / "webarena"
_RUNNER = _PROJECT_ROOT / "scripts" / "webarena_runner.py"

_FEATURES = ["tool_selection", "tool_args", "multi_step_reasoning"]
_TOOLS = ["browser"]
# The hosted-site env vars WebArena needs (operator provisions the Docker stack).
_SITE_ENV_VARS = (
    "WA_SHOPPING",
    "WA_SHOPPING_ADMIN",
    "WA_REDDIT",
    "WA_GITLAB",
    "WA_WIKIPEDIA",
    "WA_MAP",
    "WA_HOMEPAGE",
)
_TIMEOUT_SECS = float(os.environ.get("MADRAS_WEBARENA_TIMEOUT", "10800"))


def _python_exe() -> Path:
    win = _WEBARENA_DIR / ".venv" / "Scripts" / "python.exe"
    posix = _WEBARENA_DIR / ".venv" / "bin" / "python"
    return win if win.exists() else posix


def _missing_site_env() -> list[str]:
    return [v for v in _SITE_ENV_VARS if not os.environ.get(v)]


def _subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env["OPENAI_BASE_URL"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def parse_results(rewards_by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize WebArena ``{task_id: reward}`` (reward 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, reward in rewards_by_task.items():
        passed = float(reward) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"webarena-{task_id}",
                "suite_id": "webarena",
                "benchmark_family": "webarena",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class WebArenaSuite(Suite):
    """External WebArena suite — web agent over hosted multi-site tasks."""

    id: str = "webarena"
    name: str = "WebArena"
    version: str = "0.14.3"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "web-arena-x/webarena via ServiceNow BrowserGym. Needs the hosted WebArena "
        "sites (WA_* env) + Playwright — operator-provisioned. Live run is heavy."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    max_tasks: int = 0  # 0 = all tasks; set to bound a live run.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="webarena",
                suite_id=self.id,
                benchmark_family="webarena",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="WebArena hosted multi-site tasks (external, infra-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run the browsergym agent on ``model`` over the WebArena tasks.

        Raises a clear, actionable error if the isolated venv or the hosted-site
        ``WA_*`` env vars are missing — the operator must provision those first.
        """
        py = _python_exe()
        if not py.exists():
            raise FileNotFoundError(
                f"WebArena venv missing at {py}. Create .benchmarks/webarena/.venv and "
                "`uv pip install browsergym-webarena` (+ `playwright install chromium`)."
            )
        missing = _missing_site_env()
        if missing:
            raise RuntimeError(
                "WebArena sites not provisioned. Stand up the WebArena Docker stack and "
                f"set the env vars: {', '.join(missing)}."
            )
        eff_k = k if k is not None else 1
        try:
            proc = subprocess.run(
                [str(py), str(_RUNNER), "--model", model, "--max-tasks", str(self.max_tasks)],
                cwd=str(_WEBARENA_DIR),
                env=_subprocess_env(),
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"WebArena run timed out after {_TIMEOUT_SECS:.0f}s "
                "(raise MADRAS_WEBARENA_TIMEOUT / lower max_tasks)."
            ) from exc
        _, sep, payload = proc.stdout.partition("__WEBARENA__")
        if not sep:
            raise RuntimeError("WebArena runner produced no results; check the run output.")
        rewards = cast("dict[str, float]", json.loads(payload))
        return parse_results(rewards, eff_k)
