"""Suite health probe — which of the 14 registered suites are runnable now vs
infra-pending. ``native``/``dataset`` suites load cases directly (no external infra);
``external`` suites declare their infra need via ``NEEDS_INFRA`` (the heavy ones we
flag rather than stand up). See suites/RUNBOOK.md for stand-up steps.
"""

from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.suites import SUITES

# external suite id -> the infra it needs to run (documented in RUNBOOK.md)
NEEDS_INFRA: dict[str, str] = {
    "tau2": "litellm",
    "swebench": "docker+litellm",
    "terminal_bench": "wsl+docker",
    "appworld": "docker(app-server)+litellm",
    "webarena": "hosted-docker-sites+litellm",
    "agentbench": "docker(task-controller+8-servers)+litellm",
}


def probe_suite(name: str) -> dict[str, Any]:
    """Probe one suite: id, kind, runnable, needs_infra, n_cases."""
    suite = SUITES.get(name)
    if suite is None:
        return {
            "id": name,
            "kind": "?",
            "runnable": False,
            "needs_infra": False,
            "n_cases": 0,
            "error": "unknown suite",
        }
    needs_infra = name in NEEDS_INFRA
    n_cases = 0
    runnable = True
    error = ""
    if suite.kind in ("native", "dataset"):
        try:
            n_cases = len(suite.load_cases())
        except Exception as exc:  # gated dataset (HF token) / parse issue
            runnable = False
            error = f"{type(exc).__name__}: {exc}"
    else:
        # external: runnable in principle, but gated on its infra being up
        runnable = not needs_infra or name == "tau2"  # tau2 only needs litellm (often up)
    return {
        "id": name,
        "kind": suite.kind,
        "runnable": runnable,
        "needs_infra": needs_infra,
        "n_cases": n_cases,
        "infra": NEEDS_INFRA.get(name, ""),
        "error": error,
    }


def probe_all() -> list[dict[str, Any]]:
    return [probe_suite(name) for name in SUITES]
