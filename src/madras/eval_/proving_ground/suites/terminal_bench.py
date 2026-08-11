"""terminal-bench external suite adapter (isolated subprocess, WSL-backed).

terminal-bench (``laude-institute/terminal-bench``) runs each task inside a
Docker container and drives an agent (``terminus``) through a tmux session. The
harness is POSIX-only: it constructs container-side paths with ``pathlib.Path``,
which corrupt to backslashes on a Windows host (``Path("/tmp")`` -> ``\\tmp``),
so ``put_archive`` 404s before the agent ever runs. The spike confirmed this is a
hard Windows-host blocker, and that the SAME run succeeds end-to-end (Docker +
LiteLLM routing + pytest grading) when the harness executes inside WSL Linux
against the shared Docker daemon.

This adapter therefore shells the harness THROUGH WSL — never imported in-process,
never run as a Windows binary. It lives in its own venv under
``~/.madras-benchmarks/terminal-bench`` inside the WSL distro (see
``scripts/setup_terminal_bench.sh``).

Routing: ``terminus`` calls ``litellm.completion(model=..., api_base=...)``. An
``openai/<name>`` model id plus ``OPENAI_API_BASE`` / ``OPENAI_API_KEY`` points it
at our LiteLLM proxy (reachable from WSL at ``http://localhost:4000``). The master
key is read from ``settings`` at runtime and injected into the WSL command env
only — never logged, never written into result rows or committed files.

The harness writes ``runs/<run_id>/results.json`` (a ``BenchmarkResults`` dump:
``results[].task_id`` + ``results[].is_resolved`` bool, one entry per attempt).
This adapter parses that into per-task pass^k and normalizes to the
proving-ground row shape.
"""

from __future__ import annotations

import json
import math
import os
import shlex
import subprocess
import sys
import uuid
from typing import Any, Literal

from pydantic import Field

from madras.config import settings
from madras.eval_.proving_ground.suite import Case, Suite

# WSL layout produced by scripts/setup_terminal_bench.sh. Overridable via env so
# a different distro / checkout location does not require a code change.
_WSL_DISTRO = os.environ.get("MADRAS_TB_WSL_DISTRO", "OutkastUbuntu2")
_WSL_TB_DIR = os.environ.get("MADRAS_TB_WSL_DIR", "/home/brijesh/.madras-benchmarks/terminal-bench")
# Inside WSL the proxy is reachable on localhost (confirmed in the spike). The
# Windows-side settings URL may be host.docker.internal / a container DNS name,
# so we default to localhost but honor an explicit override.
_WSL_PROXY_BASE = os.environ.get("MADRAS_TB_WSL_PROXY_BASE", "http://localhost:4000")
# terminal-bench ships its hand-crafted tasks under original-tasks/; --dataset
# would fetch terminal-bench-core==head over the network. We default to the
# vendored path so a run is reproducible and offline-capable.
_TB_DATASET_PATH = os.environ.get("MADRAS_TB_DATASET_PATH", "original-tasks")
# Wall-clock cap so a wedged Docker task (image-pull stall, tmux hang) can't block
# a sweep worker forever. Generous default; override via env for big task sets.
_TB_TIMEOUT_SECS = float(os.environ.get("MADRAS_TB_TIMEOUT", "1800"))

_FEATURES = [
    "tool_selection",
    "tool_args",
    "multi_step_reasoning",
    "governance_rank_gate",
]
_TOOLS = ["shell"]


def _proxy_model(model: str) -> str:
    """Make ``model`` route through our OpenAI-compatible LiteLLM proxy.

    ``terminus`` passes the id straight to ``litellm.completion``; an ``openai/``
    prefix tells litellm to use the OpenAI-compatible path (our proxy) rather than
    guessing a provider from the bare name.
    """
    return model if "/" in model else f"openai/{model}"


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


def parse_results(results: dict[str, Any], k: int) -> list[dict[str, Any]]:
    """Normalize one terminal-bench ``results.json`` into pg rows.

    Groups trials by ``task_id``, counts resolved trials (``is_resolved`` true),
    and computes per-task pass^k. Trials whose ``is_resolved`` is null (an
    infrastructure/agent error that never produced a verdict) are excluded from
    the trial count, matching τ²-bench's treatment of infra errors.
    """
    grouped: dict[str, list[bool]] = {}
    trials_in: list[dict[str, Any]] = results.get("results") or []
    for trial in trials_in:
        resolved = trial.get("is_resolved")
        if resolved is None:
            continue
        task_id = str(trial.get("task_id"))
        grouped.setdefault(task_id, []).append(bool(resolved))

    rows: list[dict[str, Any]] = []
    for task_id in sorted(grouped, key=lambda t: (len(t), t)):
        outcomes = grouped[task_id]
        trials = len(outcomes)
        passes = sum(1 for ok in outcomes if ok)
        eff_k = max(1, min(k, trials))
        rows.append(
            {
                "scenario_id": f"terminal_bench-{task_id}",
                "suite_id": "terminal_bench",
                "benchmark_family": "terminal_bench",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": passes,
                "pass_rate": _pass_hat_k(trials, passes, eff_k),
            }
        )
    return rows


class TerminalBenchSuite(Suite):
    """External self-running terminal-bench suite (Docker + tmux, WSL-backed)."""

    id: str = "terminal_bench"
    name: str = "terminal-bench"
    version: str = "0.2.18"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = "laude-institute/terminal-bench"
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    # Specific task ids to run. Empty -> the harness's --n-tasks slice of the
    # dataset (keeps a sweep bounded without hand-listing every task).
    task_ids: list[str] = Field(default_factory=lambda: ["hello-world"])
    n_tasks: int = 1
    k: int = 2

    def load_cases(self) -> list[Case]:
        """Lightweight per-task Case stubs for coverage metadata.

        terminal-bench drives its own Docker/tmux loop, so these are not executed
        through our governed runner — they exist so the suite registry can
        aggregate feature/tool coverage.
        """
        return [
            Case(
                id=f"terminal_bench-{task_id}",
                suite_id=self.id,
                benchmark_family="terminal_bench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt=f"terminal-bench {task_id} (external Docker task)",
                k=self.k,
            )
            for task_id in self.task_ids
        ]

    def _build_wsl_script(self, model: str, attempts: int, concurrency: int, run_id: str) -> str:
        """Bash run, executed inside WSL, that shells ``tb run`` and emits the
        results.json to stdout between sentinels so the parent can recover it.

        The LiteLLM key is referenced as ``$OPENAI_API_KEY`` (injected via the WSL
        command env) — never interpolated into the script text.
        """
        # terminal-bench rejects --task-id together with --n-tasks. Explicit task
        # ids win (a fixed, reproducible slice); otherwise fall back to the first
        # --n-tasks of the dataset.
        if self.task_ids:
            select_flags = "".join(f" --task-id {shlex.quote(t)}" for t in self.task_ids)
        else:
            select_flags = f" --n-tasks {int(self.n_tasks)}"
        return (
            f"set -euo pipefail; cd {shlex.quote(_WSL_TB_DIR)}; "
            f"rm -rf runs/{shlex.quote(run_id)}; "
            "PYTHONUTF8=1 PYTHONIOENCODING=utf-8 "
            f"OPENAI_API_BASE={shlex.quote(_WSL_PROXY_BASE)} "
            "./.venv/bin/tb run "
            f"--dataset-path {shlex.quote(_TB_DATASET_PATH)}"
            f"{select_flags} "
            "--agent terminus "
            f"--model {shlex.quote(_proxy_model(model))} "
            f"--n-attempts {int(attempts)} "
            f"--n-concurrent {int(concurrency)} "
            f"--run-id {shlex.quote(run_id)} "
            "--output-path runs --no-cleanup --no-livestream "
            ">/dev/null 2>&1; "
            'echo "__MADRAS_TB_RESULTS__"; '
            f"cat runs/{shlex.quote(run_id)}/results.json"
        )

    def _run(self, model: str, attempts: int, concurrency: int) -> dict[str, Any]:
        """Shell ``tb run`` inside WSL and return the parsed results.json."""
        run_id = f"madras_{uuid.uuid4().hex[:12]}"
        script = self._build_wsl_script(model, attempts, concurrency, run_id)
        env = dict(os.environ)
        env["OPENAI_API_KEY"] = settings.litellm_master_key or "sk-noauth"
        env["WSLENV"] = "OPENAI_API_KEY/u:" + env.get("WSLENV", "")
        cmd = ["wsl.exe", "-d", _WSL_DISTRO, "--", "bash", "-c", script]
        try:
            proc = subprocess.run(
                cmd,
                env=env,
                check=True,
                capture_output=True,
                text=True,
                timeout=_TB_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"terminal-bench timed out after {_TB_TIMEOUT_SECS:.0f}s "
                f"(a Docker task likely wedged). Raise MADRAS_TB_TIMEOUT if expected."
            ) from exc
        _, sep, payload = proc.stdout.partition("__MADRAS_TB_RESULTS__")
        if not sep or not payload.strip():
            raise RuntimeError(
                "terminal-bench produced no results.json. Run "
                "scripts/setup_terminal_bench.sh and confirm WSL + Docker are up."
            )
        return json.loads(payload)

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Run terminal-bench for ``model``; return normalized pg rows.

        ``k`` controls both ``--n-attempts`` (trials per task) and the pass^k
        order (defaults to ``self.k``).
        """
        eff_k = k if k is not None else self.k
        results = self._run(model, eff_k, concurrency)
        return parse_results(results, eff_k)


if __name__ == "__main__":  # pragma: no cover - manual live smoke
    suite = TerminalBenchSuite(k=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    print(json.dumps(suite.run(sys.argv[1] if len(sys.argv) > 1 else "llama-70b")))
