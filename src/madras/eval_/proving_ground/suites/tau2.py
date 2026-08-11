"""τ²-bench external suite adapter (isolated subprocess).

τ²-bench (sierra-research/tau2-bench) runs its OWN agent/user simulation loop and
pins deps (litellm/pydantic, Python 3.12+) that conflict with the main Madras env.
It therefore installs into its own venv under ``.benchmarks/tau2-bench`` (see
``scripts/setup_tau2.sh``) and is driven here via ``subprocess`` — never imported
in-process.

Routing: τ²-bench calls ``litellm.completion(model=...)`` directly, so an
OpenAI-compatible model id (``openai/<name>``) plus ``OPENAI_API_BASE`` /
``OPENAI_API_KEY`` env points it at our LiteLLM proxy. The key is read from
``settings`` at runtime and injected via the subprocess env only — never logged,
never written into result rows.

This adapter shells ``tau2 run`` for the agent model + domains, then parses the
real per-task pass^k from the written ``results.json`` and normalizes it into the
proving-ground row shape.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

# Repo + isolated venv layout produced by scripts/setup_tau2.sh.
_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_TAU2_DIR = _PROJECT_ROOT / ".benchmarks" / "tau2-bench"
_TAU2_DATA_DIR = _TAU2_DIR / "data"

_FEATURES = ["tool_selection", "tool_args", "multi_turn_consistency"]


def _tau2_executable() -> Path:
    """Path to the isolated-venv tau2 CLI (Windows .exe or POSIX bin)."""
    win = _TAU2_DIR / ".venv" / "Scripts" / "tau2.exe"
    posix = _TAU2_DIR / ".venv" / "bin" / "tau2"
    return win if win.exists() else posix


def _proxy_model(model: str) -> str:
    """Make ``model`` route through our OpenAI-compatible LiteLLM proxy.

    τ²-bench passes the id straight to ``litellm.completion``; an ``openai/``
    prefix tells litellm to use the OpenAI-compatible path (our proxy) rather than
    guessing a provider from the bare name.
    """
    return model if "/" in model else f"openai/{model}"


def _subprocess_env() -> dict[str, str]:
    """Env that points τ²-bench at our LiteLLM proxy + bundled data.

    ``litellm_base_url`` already carries the ``/v1`` suffix the OpenAI-compatible
    client expects. The master key is injected here only; it is never persisted.
    PYTHONUTF8/PYTHONIOENCODING avoid the Windows cp1252 crash on τ²-bench's
    emoji output.
    """
    env = dict(os.environ)
    env["OPENAI_API_BASE"] = settings.litellm_base_url
    env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
    env["TAU2_DATA_DIR"] = str(_TAU2_DATA_DIR)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _pass_hat_k(num_trials: int, success_count: int, k: int) -> float:
    """pass^k for a single task (Sierra's definition; arxiv 2406.12045).

    Probability that a random size-``k`` subset of the ``num_trials`` attempts is
    all-success. Clamped so k never exceeds the trials actually present.
    """
    k = min(k, num_trials)
    if k <= 0 or num_trials <= 0:
        return 0.0
    if success_count < k:
        return 0.0
    return math.comb(success_count, k) / math.comb(num_trials, k)


def _is_success(reward: float | None) -> bool:
    if reward is None:
        return False
    return (1 - 1e-6) <= reward <= (1 + 1e-6)


def parse_results(results: dict[str, Any], domain: str, k: int) -> list[dict[str, Any]]:
    """Normalize one τ²-bench ``results.json`` (one domain) into pg rows.

    Groups simulations by ``task_id``, counts successful trials (reward == 1),
    and computes per-task pass^k. ``infrastructure_error`` sims are excluded from
    the trial count (they never really ran), matching τ²-bench's own metrics.
    """
    info: dict[str, Any] = results.get("info") or {}
    num_trials = int(info.get("num_trials") or 0)

    grouped: dict[str, list[float | None]] = {}
    simulations: list[Any] = results.get("simulations") or []
    for raw_sim in simulations:
        sim = cast("dict[str, Any]", raw_sim)
        if sim.get("termination_reason") == "infrastructure_error":
            continue
        task_id = str(sim.get("task_id"))
        reward_info: dict[str, Any] = sim.get("reward_info") or {}
        grouped.setdefault(task_id, []).append(reward_info.get("reward"))

    rows: list[dict[str, Any]] = []
    for task_id in sorted(grouped, key=lambda t: (len(t), t)):
        rewards = grouped[task_id]
        trials = len(rewards) or num_trials
        passes = sum(1 for r in rewards if _is_success(r))
        eff_k = max(1, min(k, trials))
        rows.append(
            {
                "scenario_id": f"tau2-{domain}-{task_id}",
                "suite_id": "tau2",
                "benchmark_family": "tau2",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": passes,
                "pass_rate": _pass_hat_k(trials, passes, eff_k),
            }
        )
    return rows


class Tau2Suite(Suite):
    """External self-running τ²-bench suite (retail/airline/telecom)."""

    id: str = "tau2"
    name: str = "tau2-bench"
    version: str = "1.0.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = "sierra-research/tau2-bench"
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    domains: list[str] = Field(default_factory=lambda: ["retail"])
    k: int = 2

    def load_cases(self) -> list[Case]:
        """Lightweight per-domain Case stubs for coverage metadata.

        τ²-bench drives its own loop, so these are not executed through our
        governed runner — they exist so the suite registry can aggregate coverage.
        """
        return [
            Case(
                id=f"tau2-{domain}",
                suite_id=self.id,
                benchmark_family="tau2",
                features=list(_FEATURES),
                prompt=f"τ²-bench {domain} domain (external self-running suite)",
                k=self.k,
            )
            for domain in self.domains
        ]

    def _run_domain(self, model: str, domain: str, trials: int, concurrency: int) -> dict[str, Any]:
        """Shell ``tau2 run`` for one domain and load its results.json."""
        exe = _tau2_executable()
        if not exe.exists():
            raise FileNotFoundError(
                f"τ²-bench not installed at {exe}. Run scripts/setup_tau2.sh first."
            )
        save_to = f"madras_{domain}_{model.replace('/', '_')}"
        cmd = [
            str(exe),
            "run",
            "--domain",
            domain,
            "--agent-llm",
            _proxy_model(model),
            "--user-llm",
            _proxy_model(model),
            "--num-trials",
            str(trials),
            "--max-concurrency",
            str(concurrency),
            "--save-to",
            save_to,
            "--log-level",
            "ERROR",
        ]
        subprocess.run(cmd, env=_subprocess_env(), check=True, capture_output=True)

        results_path = _TAU2_DATA_DIR / "simulations" / save_to / "results.json"
        if not results_path.exists():
            raise FileNotFoundError(f"τ²-bench produced no results at {results_path}")
        with results_path.open(encoding="utf-8") as fh:
            return json.load(fh)

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run τ²-bench for ``model`` across ``self.domains``; return pg rows.

        ``k`` controls both the number of trials requested and the pass^k order
        (defaults to ``self.k``).
        """
        eff_k = k if k is not None else self.k
        rows: list[dict[str, Any]] = []
        for domain in self.domains:
            results = self._run_domain(model, domain, eff_k, concurrency)
            rows.extend(parse_results(results, domain, eff_k))
        return rows


if __name__ == "__main__":  # pragma: no cover - manual live smoke
    suite = Tau2Suite(domains=["retail"], k=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    print(json.dumps(suite.run(sys.argv[1] if len(sys.argv) > 1 else "llama-70b")))
