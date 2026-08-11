"""ITBench external suite adapter (snapshot mode; remote OCI VM via SSH) — W5·F·remote.

ITBench (IBM-research/ITBench-Lite) evaluates IT-automation agents on incident-response /
root-cause-analysis scenarios. Unlike the other external suites, ITBench is **not installed
locally** — it lives on the OCI VM ``outkast-engine`` (public IP at ``.benchmarks/_oci_ip.txt``),
where ``~/benchmarks/itbench-agent`` is a uv project exposing the ``itbench-eval`` console script.
Snapshot mode (no k8s cluster) scores a custom agent's output artifacts against ground-truth
snapshots drawn from HF ``ibm-research/ITBench-Lite``:

    uv run itbench-eval --ground-truth <snapshot_dir> --outputs <agent_outputs_dir> \\
        --eval-criteria ROOT_CAUSE_ENTITY ROOT_CAUSE_REASONING --result-file <out.json>

Because the eval runs remotely, this adapter's ``run`` drives ``scripts/itbench_runner.py``
**locally** (a plain Python subprocess), which SSHes to the VM (``ssh -i ~/.ssh/oci_key
ubuntu@<ip>``) to invoke ``itbench-eval`` over a snapshot + outputs dir already present on the VM,
reads back the result JSON, and prints per-scenario scores after a sentinel. RCA scores are
**graded** (0..1), so a scenario counts as passing at ``score >= 0.5``.

BUILT + registered (discoverable in the proving ground). A live run is gated on the SSH key
(``~/.ssh/oci_key``) and the OCI IP file — ``run`` raises a clear, actionable error until both are
present. The hermetic test pins metadata + parsing + the SSH-key/IP gate.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_PROJECT_ROOT = Path(__file__).resolve().parents[5]
_OCI_IP_FILE = _PROJECT_ROOT / ".benchmarks" / "_oci_ip.txt"
_RUNNER = _PROJECT_ROOT / "scripts" / "itbench_runner.py"
_SSH_KEY = Path.home() / ".ssh" / "oci_key"
_SENTINEL = "__ITBENCH__"

_FEATURES = [
    "incident_response",
    "root_cause_analysis",
    "multi_step_reasoning",
    "tool_selection",
]
_TOOLS = ["shell", "kubernetes"]
# RCA scores are graded (0..1); a scenario passes at/above this threshold.
_PASS_THRESHOLD = 0.5
# Default snapshot ground-truth path (on the VM); override per run via the `scenario` field.
_DEFAULT_SCENARIO = "~/benchmarks/itbench-agent/snapshots/sre/Scenario-1"
_TIMEOUT_SECS = float(os.environ.get("MADRAS_ITBENCH_TIMEOUT", "3600"))


def _oci_ip() -> str | None:
    """Read the OCI VM public IP from ``.benchmarks/_oci_ip.txt`` (None if absent/empty)."""
    if not _OCI_IP_FILE.exists():
        return None
    ip = _OCI_IP_FILE.read_text(encoding="utf-8").strip()
    return ip or None


def parse_results(by_scenario: dict[str, float], k: int) -> list[dict[str, Any]]:
    """Normalize ITBench ``{scenario: score}`` (graded 0..1) into pg rows; pass at >= 0.5."""
    rows: list[dict[str, Any]] = []
    eff_k = max(1, k)
    for scenario, score in by_scenario.items():
        passed = float(score) >= _PASS_THRESHOLD
        rows.append(
            {
                "scenario_id": f"itbench-{scenario}",
                "suite_id": "itbench",
                "benchmark_family": "itbench",
                "features": list(_FEATURES),
                "k": eff_k,
                "passes": eff_k if passed else 0,
                "pass_rate": 1.0 if passed else 0.0,
            }
        )
    return rows


class ItBenchSuite(Suite):
    """External ITBench suite — snapshot-mode RCA, scored on a remote OCI VM over SSH."""

    id: str = "itbench"
    name: str = "ITBench"
    version: str = "0.2.0"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "ibm-research/ITBench-Lite (snapshot mode). Incident-response / root-cause-analysis "
        "scenarios scored by `itbench-eval` on a remote OCI VM (outkast-engine) over SSH; "
        "graded 0..1. Live run is SSH-key- + OCI-IP-gated."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    tools: list[str] = Field(default_factory=lambda: list(_TOOLS))
    eval_criteria: list[str] = Field(default_factory=lambda: ["ROOT_CAUSE_ENTITY"])
    scenario: str = _DEFAULT_SCENARIO  # ground-truth snapshot dir on the VM
    outputs: str = "~/benchmarks/itbench-agent/outputs/agent_outputs"  # agent artifacts on the VM

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id="itbench",
                suite_id=self.id,
                benchmark_family="itbench",
                features=list(_FEATURES),
                tools=list(_TOOLS),
                prompt="ITBench snapshot-mode RCA scenarios (external, remote OCI VM, SSH-gated)",
            )
        ]

    def run(self, model: str, k: int | None = None, concurrency: int = 1) -> list[dict[str, Any]]:
        """Drive the local runner (which SSHes to the OCI VM). Raises clearly if the gate fails."""
        if not _SSH_KEY.exists():
            raise FileNotFoundError(
                f"OCI SSH key missing at {_SSH_KEY}. ITBench runs on the remote VM "
                "outkast-engine; place the key at ~/.ssh/oci_key, then retry."
            )
        ip = _oci_ip()
        if ip is None:
            raise FileNotFoundError(
                f"OCI IP file missing/empty at {_OCI_IP_FILE}. Write the VM public IP there "
                "(e.g. 144.24.116.7), then retry."
            )
        eff_k = k if k is not None else 1
        cmd = [
            "python",
            str(_RUNNER),
            "--scenario",
            self.scenario,
            "--outputs",
            self.outputs,
            "--eval-criteria",
            *self.eval_criteria,
        ]
        try:
            proc = subprocess.run(
                cmd,
                env={**os.environ, "PYTHONUTF8": "1"},
                check=True,
                capture_output=True,
                text=True,
                timeout=_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"ITBench run timed out after {_TIMEOUT_SECS:.0f}s (check the OCI VM / SSH)."
            ) from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                f"ITBench runner failed (rc={exc.returncode}); SSH/eval error:\n"
                f"{(exc.stderr or '').strip()[-2000:]}"
            ) from exc
        _, sep, payload = proc.stdout.partition(_SENTINEL)
        if not sep:
            raise RuntimeError("ITBench runner produced no results; check the run output.")
        return parse_results(cast("dict[str, float]", json.loads(payload)), eff_k)
