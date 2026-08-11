"""Suite x Subsystem coverage — benchmark-design.md §12d (BD10, s42).

Maps every registered suite to the Madras subsystem(s) it genuinely exercises, then computes
real coverage counts from that mapping — data-derived, not a one-time read-through judgment call
(BD10). Re-run whenever the suite roster changes (a new suite must be tagged here or
``test_every_registered_suite_is_tagged`` fails the lock).

The five subsystems (per §12d): the engine itself, the agents built on it, the compile/optimize
loop, the capability catalog, and the Builder UX pipeline as a product.
"""

from __future__ import annotations

from typing import Any

SUBSYSTEMS = (
    "framework",
    "agents",
    "compiler",
    "capabilities",
    "builder",
    "skills",
    "tools",
    "marketplace",
)

# suite_id -> the subsystem(s) it genuinely exercises (many-to-many; a suite can test more than
# one). Framework = engine mechanics regardless of model/agent. Agents = end-to-end task
# competence per agent. Compiler = the compile->verify->optimize loop itself. Capabilities =
# one capability lane isolated. Builder = the Builder UX/compile-to-residency pipeline as a
# product (distinct from the agent it produces).
SUITE_SUBSYSTEMS: dict[str, tuple[str, ...]] = {
    # Framework / Agent OS — engine mechanics, model-agnostic
    "identity_boundary_conformance": ("framework",),
    "routing_resilience_conformance": ("framework",),
    "durable_state_conformance": ("framework",),
    "memory_sovereignty_conformance": ("framework",),
    "compounding": ("framework",),
    "mcppoison": ("framework", "capabilities"),
    "madras_features": ("framework", "agents"),
    "compile_conformance": ("framework", "compiler"),
    "gepa_evolve_conformance": ("compiler",),  # closes the optimize()-convergence gap, §12k
    # Agents — end-to-end task competence
    "gaia": ("agents",),
    "swebench": ("agents",),
    "webarena": ("agents",),
    "appworld": ("agents",),
    "agentbench": ("agents",),
    "osworld": ("agents",),
    "embodiedbench": ("agents",),
    "tau2": ("agents",),
    "terminal_bench": ("agents",),
    "webvoyager": ("agents",),
    "androidworld": ("agents",),
    "marble": ("agents",),
    "collab_overcooked": ("agents",),
    "asb": ("agents",),
    "webwalkerqa": ("agents",),
    "rcaeval": ("agents",),
    "openrca": ("agents",),
    "itbench": ("agents",),
    "agentdojo": ("agents", "capabilities"),
    # Capabilities — one capability lane isolated
    "bfcl": ("capabilities",),
    "mcpuniverse": ("capabilities",),
    "mcpatlas": ("capabilities",),
    "mcptoolbench": ("capabilities",),
    "toolathlon": ("capabilities",),
    "longmemeval": ("capabilities",),
    "locomo": ("capabilities",),
    "memoryagentbench": ("capabilities",),
    "epmemory": ("capabilities",),
    "knowedit": ("capabilities",),
    "legalbench": ("agents", "capabilities"),  # fills the Atticus (Legal, vs Harvey) gap, §12f
    "marketing_agent": ("agents", "capabilities"),  # fills the Maverick (Marketing) gap, §12f
    "financebenchmark": ("agents", "capabilities"),  # fills the Mona (Finance/CFO) gap, §12f
    "eqbench_creative": ("agents", "capabilities"),  # fills Andy (creative WRITING), §12f
    "recruiting_agent": ("agents", "capabilities"),  # fills the Joy (Recruiting) gap, §12f
    "strategy_agent": (
        "agents",
        "capabilities",
    ),  # fills the Sage (Strategy) gap, §12j — last vertical
    "communication_reach": ("capabilities",),  # fills Messaging & Reach only (1/9), §12f
    "creative_media_generation": ("capabilities",),  # closes Creative & Media media-gen, §12i
    "voice_telephony_multilingual": ("capabilities",),  # closes remaining 8/9 Comm&Reach, §12i
    "skill_retrieval_conformance": ("skills",),  # fills the Skills subsystem gap, §12f
    "tool_reliability_conformance": ("tools",),  # fills the Tools subsystem gap, §12f
    "arc_agi2": ("agents", "capabilities"),  # §12a frontier-difficulty suite (Frontier category)
    "swebench_pro": ("agents", "capabilities"),  # §12a — succeeds swebench Verified as the gate
    "marketplace_gate_conformance": ("marketplace", "compiler"),  # new subsystem, s42
    "builder_pipeline_conformance": ("builder",),  # closes the confirmed 0-suite gap, §12g
    "tamper_evident_audit_conformance": ("framework",),  # Benchmark.md §6 axis #5
    "delegation_isolation_conformance": ("framework",),  # Benchmark.md §6 axis #6
    "hitl_absorption_conformance": ("framework",),  # Benchmark.md §6 axis #8
    "scheduler_reliability_conformance": ("framework",),  # Benchmark.md §6 axis #9
    "verify_pool_robustness_conformance": ("framework",),  # Benchmark.md §6 axis #10
    "agentharm": ("capabilities",),
    "agentsafety": ("capabilities",),
    "injecagent": ("capabilities",),
    "questclarify": ("capabilities",),
    "abgcoqa": ("capabilities",),
    "clamber": ("capabilities",),
    "personagym": ("capabilities",),
    "rolebench": ("capabilities",),
    "conflictqa": ("capabilities",),
    "screenspot": ("capabilities",),
    "gpqa": ("capabilities",),
    "gsm8k": ("capabilities",),
    "mmlu_pro": ("capabilities",),
    "aime": ("capabilities",),
    "hle": ("capabilities",),
    "frames": ("capabilities",),
    "sealqa": ("capabilities",),
    "livebench": ("capabilities",),
    "browsecomp": ("capabilities",),
    "gdpval": ("capabilities",),
    "metr": ("capabilities", "agents"),
    # Builder — no suite tests the Builder UX/compile-to-residency pipeline itself (confirmed
    # gap, benchmark-design.md §12d). Intentionally left with zero entries here.
}


def _verdict(suite_count: int) -> str:
    """0 suites -> gap; 1-2 -> thin; 3+ -> adequate (§12d thresholds)."""
    if suite_count <= 0:
        return "gap"
    if suite_count <= 2:
        return "thin"
    return "adequate"


def subsystem_coverage() -> dict[str, dict[str, Any]]:
    """Real per-subsystem coverage, computed from ``SUITE_SUBSYSTEMS`` — not asserted."""
    by_subsystem: dict[str, list[str]] = {s: [] for s in SUBSYSTEMS}
    for suite_id, tags in SUITE_SUBSYSTEMS.items():
        for tag in tags:
            by_subsystem[tag].append(suite_id)
    return {
        subsystem: {
            "suites": sorted(suite_ids),
            "suite_count": len(suite_ids),
            "verdict": _verdict(len(suite_ids)),
        }
        for subsystem, suite_ids in by_subsystem.items()
    }
