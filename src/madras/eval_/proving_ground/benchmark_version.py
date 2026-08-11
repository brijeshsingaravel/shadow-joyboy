"""Madras Benchmark v3 — the locked version manifest (W0·A5, re-frozen s42 §12).

W0 froze the Madras Benchmark at v1; framework-10x Part C re-froze it at v2 (C7, s34) after
adding 5 deterministic conformance suites for the s33 capability classes (Identity & Privilege
Boundary / Routing-resilience / Durable-state / Compile-conformance / Memory-sovereignty —
C1-C5) + an outlier moat-signal panel (C6). **s42's full 9-subsystem audit (benchmark-design.md
§12h-§12p — Framework/Capabilities/Agents/Compiler/Builder all found+closed real gaps; Proving
Ground/Marketplace/Skills/Tools confirmed adequate) re-froze it at v3**: 58 → 79 suites (frontier
suites ARC-AGI-2 + SWE-bench Pro; the 5 vertical-gap suites legalbench/marketing_agent/
financebenchmark/eqbench_creative/recruiting_agent + strategy_agent closing all 9 launch-roster
verticals; creative_media_generation + voice_telephony_multilingual closing the corrected
Capabilities gaps; 5 Framework proprietary-axis conformance suites; gepa_evolve_conformance
closing the Compiler measurement gap; procedural_regen methodology). This module is the single
source the three surfaces (Lighthouse, Shadow cockpit, website) read to show "Madras Benchmark
v3". Bumping the benchmark (adding/removing a suite, changing methodology) requires bumping
``VERSION`` here and the lock test in ``test_benchmark_version.py`` - so a roster change can
never ship silently.
"""

from __future__ import annotations

from typing import Any

from madras.eval_.proving_ground.suites import SUITES

VERSION = "v3"  # re-frozen s42 (benchmark-design.md §12h-§12p, the full 9-subsystem audit).
FROZEN_SESSION = 42  # the session that locked v3 (the 9-subsystem audit, §12h-§12p).
FROZEN_DATE = "2026-07-07"  # v3's freeze date.

METHODOLOGY = {
    "held_out": "Tune on dev, gate + publish on held-out (see heldout.py); outlier suites are "
    "whole-suite held-out gate sets.",
    "targets": "Per-benchmark targets are set now (spec/default) except the 2 novel moat metrics, "
    "which stay blank until Run #1 (targets.py TARGET_SOURCE='run1', reserved for "
    "moat_metrics only as of C7).",
    "index": "Tier-weighted composite (index.py): free=capability, premium=control/moat, "
    "byok=balanced; the compounding-efficiency signature is weighted at the moat tier.",
    "moat_metrics": ["compounding", "mcppoison"],  # Madras-original, un-gameable
    "scoring": "Deterministic checks (answer_regex / bbox_hit / tool) + judge rubrics; "
    "held-out + multi-seed CI per the eval-rigor discipline.",
    "conformance": "5 deterministic zero-LLM conformance suites (C1-C5) gate the s33 architectural "
    "moat (identity_boundary/routing_resilience/durable_state/compile/memory) at a "
    "1.0 correctness invariant, surfaced via outlier_verdict's moat signals (C6).",
}


def benchmark_manifest() -> dict[str, Any]:
    """Return the locked v1 manifest (version, roster, methodology) for the surfaces to display."""
    roster = sorted(SUITES.keys())
    return {
        "version": VERSION,
        "frozen_session": FROZEN_SESSION,
        "frozen_date": FROZEN_DATE,
        "suite_count": len(roster),
        "suites": roster,
        "methodology": METHODOLOGY,
    }
