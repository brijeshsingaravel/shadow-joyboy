"""EmbodiedBench external suite adapter (embodied agents in AI2-THOR, runs in WSL2 + conda).

EmbodiedBench (EmbodiedBench/EmbodiedBench) evaluates multi-modal agents on embodied tasks —
navigation, manipulation, long-horizon planning — inside the AI2-THOR 3D simulator. The sim needs
a CUDA GPU + a headless X server, and the stack is installed in a WSL2 conda env (``embench_nav``),
so — unlike the other externals which use a Windows ``.venv`` — this one is driven through
``wsl.exe`` running the conda-env Python. The benchmark is Hydra-driven; its ``openai`` catch-all
provider reads ``remote_url`` and instantiates ``OpenAI(base_url=remote_url)`` for any model id that
does NOT contain ``gpt``/``claude``/``gemini``/``qwen`` — so we alias the model with a ``madras-``
prefix and point ``remote_url`` at our LiteLLM gateway. ``scripts/embodiedbench_runner.py`` starts
the X server, runs the episodes, and prints per-task success after a sentinel.

BUILT + registered (discoverable in the proving ground). A live run is gated on the WSL conda env
+ a working GPU render — ``run`` raises a clear, actionable error until both are present. The
hermetic test pins metadata + parsing + the WSL/conda gate.
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
_RUNNER = _PROJECT_ROOT / "scripts" / "embodiedbench_runner.py"
_SENTINEL = "__EMBODIEDBENCH__"

_WSL_DISTRO = "OutkastUbuntu2"
_CONDA_ENV = "embench_nav"
# Where the runner script (a Windows-side file) is reachable from inside WSL.
_WSL_RUNNER = "/mnt/o/Madras AI/Engineering/scripts/embodiedbench_runner.py"

_FEATURES = [
    "embodied_planning",
    "spatial_reasoning",
    "multi_step_reasoning",
    "long_horizon",
]
_TOOLS = ["embodied"]
_MODEL_ALIAS_PREFIX = "madras-"  # forces the openai catch-all (no gpt/claude/gemini/qwen)
_TIMEOUT_SECS = float(os.environ.get("MADRAS_EMBODIEDBENCH_TIMEOUT", "10800"))


def _wsl_check_env() -> subprocess.CompletedProcess[str]:
    """Probe that the WSL distro + the conda env exist (cheap, no sim, no GPU)."""
    cmd = (
        "source ~/miniconda3/etc/profile.d/conda.sh && "
        f"conda activate {_CONDA_ENV} && python -c 'import ai2thor, torch'"
    )
    return subprocess.run(
        ["wsl.exe", "-d", _WSL_DISTRO, "bash", "-lc", cmd],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _alias_model(model: str) -> str:
    """Alias the model id so EmbodiedBench routes it through ``OpenAI(base_url=remote_url)``.

    The catch-all provider is only chosen when the id does NOT contain gpt/claude/gemini/qwen;
    a ``madras-`` prefix guarantees that while leaving the underlying LiteLLM model name intact.
    """
    blocked = ("gpt", "claude", "gemini", "qwen")
    if any(tok in model.lower() for tok in blocked):
        return _MODEL_ALIAS_PREFIX + model
    return model


def parse_results(by_task: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize EmbodiedBench ``{task_id: success}`` (execution-based, 0/1) into pg rows."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for task_id, score in by_task.items():
        passed = float(score) >= (1.0 - 1e-6)
        rows.append(
            {
                "scenario_id": f"embodiedbench-{task_id}",
                "suite_id": "embodiedbench",
                "benchmark_family": "embodiedbench",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class EmbodiedBenchSuite(Suite):
    """External EmbodiedBench suite — embodied agents in AI2-THOR, execution-scored (WSL-gated)."""

    id: str = "embodiedbench"
    name: str = "EmbodiedBench"
    version: str = "0.1.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "EmbodiedBench/EmbodiedBench. Multi-modal embodied-agent tasks (navigation, manipulation, "
        "long-horizon planning) in the AI2-THOR 3D simulator; execution-scored. Runs in a WSL2 "
        "conda env via subprocess. Live run is GPU- + conda-env-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    env: str = "eb-nav"  # Hydra sub-env (eb-nav = EB-Navigation, lightest)
    down_sample_ratio: float = 0.1  # fraction of episodes to run (bounds a live run)
    max_tasks: int = 0  # 0 = all (after down-sampling); set to bound a live run.

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="embodiedbench",
                suite_id=self.id,
                benchmark_family="embodiedbench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="EmbodiedBench embodied-agent tasks in AI2-THOR (external, GPU + WSL-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run EmbodiedBench through WSL2 conda. Raises a clear error if the env/GPU is absent."""
        try:
            probe = _wsl_check_env()
        except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
            raise FileNotFoundError(
                f"EmbodiedBench conda env unreachable (wsl.exe -d {_WSL_DISTRO}, env "
                f"'{_CONDA_ENV}'). Ensure WSL2 + miniconda + the '{_CONDA_ENV}' env "
                "(torch + ai2thor) are installed, then retry."
            ) from exc
        if probe.returncode != 0:
            raise FileNotFoundError(
                f"EmbodiedBench conda env '{_CONDA_ENV}' in WSL distro '{_WSL_DISTRO}' is "
                f"missing or broken (import of torch/ai2thor failed):\n{probe.stderr.strip()}"
            )

        eff_k = k if k is not None else 1
        aliased = _alias_model(model)
        remote_url = settings.litellm_base_url
        api_key = settings.litellm_master_key or "sk-noauth"
        inner = (
            "source ~/miniconda3/etc/profile.d/conda.sh && "
            f"conda activate {_CONDA_ENV} && "
            f"remote_url={_sh_quote(remote_url)} "
            f"OPENAI_API_KEY={_sh_quote(api_key)} "
            f"python {_sh_quote(_WSL_RUNNER)} "
            f"--env {_sh_quote(self.env)} --model {_sh_quote(aliased)} "
            f"--down-sample-ratio {self.down_sample_ratio} --max-tasks {self.max_tasks}"
        )
        try:
            proc = subprocess.run(
                ["wsl.exe", "-d", _WSL_DISTRO, "bash", "-lc", inner],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"EmbodiedBench run timed out after {_TIMEOUT_SECS:.0f}s "
                "(lower down_sample_ratio or max_tasks)."
            ) from exc
        if proc.returncode != 0:
            raise RuntimeError(
                "EmbodiedBench run failed (sim/GPU render error). stderr:\n"
                f"{proc.stderr.strip()[-2000:]}"
            )
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError(
                "EmbodiedBench runner produced no results; check the run output (X server / "
                "GPU render may have failed)."
            )
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)


def _sh_quote(value: str) -> str:
    """Single-quote a value for the WSL bash command line."""
    return "'" + value.replace("'", "'\\''") + "'"
