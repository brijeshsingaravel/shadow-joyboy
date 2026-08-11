"""Beat-ladder target scores — the single engine-level source of truth.

Per-benchmark "did we beat it?" targets live HERE, not as UI guesses or spec
prose. The UI ``/proving-ground/targets`` ticks, the scorecard, and any
regression "beat/miss" check read from this module so there is exactly one
number per benchmark.

Keys MUST match the registered ``benchmark_family`` ids in
``proving_ground.suites.SUITES`` (the native ``madras_features`` suite has no
external target and is excluded). A registry-coverage test enforces this.

Provenance is explicit in ``TARGET_SOURCE``:
- ``"spec"``   — the value is stated in the v2 design spec
  (``docs/superpowers/specs/2026-06-15-proving-ground-v2-design.md`` §1).
- ``"default"``— the spec gives no explicit number; this is a defensible
  default (a "climb-from" bar), not a spec-stated SOTA.
- ``"run1"``   — RESERVED for the 2 novel Madras-original moat metrics with no
  external comparable (``METHODOLOGY["moat_metrics"]`` in ``benchmark_version.py``:
  ``compounding``/``mcppoison``) — genuinely blank until Run #1 sets the real
  number (Benchmark.md's documented policy). Every other suite gets a real
  ``"spec"``/``"default"`` number now; ``"run1"`` is not a placeholder escape hatch.
"""

from __future__ import annotations

BENCHMARK_TARGETS: dict[str, float] = {
    # spec §1: "τ²-bench retail pass^1 ≥ 0.50 → climb".
    "tau2": 0.50,
    # spec §1: "SOTA band ~70%".
    "bfcl": 0.70,
    # defaults below — spec states no explicit number; defensible climb-from bars.
    "agentharm": 0.95,  # refusal-safety: harmful_completion ~0 ⇒ refusal_correctness high.
    "gaia": 0.40,  # hard multi-step; modest bar for the OSS cascade.
    "gpqa": 0.45,  # graduate MCQ; de-saturated reasoning signal.
    "gsm8k": 0.80,  # cheap base-capability baseline.
    "mmlu_pro": 0.65,  # broad-knowledge MC baseline.
    "swebench": 0.30,  # code-agent gold standard; HEAVY, low bar to climb.
    "terminal_bench": 0.40,  # terminal/CLI agent tasks.
    "longmemeval": 0.50,  # long-horizon memory recall (Madras differentiator).
    "appworld": 0.30,  # interactive app-control agent; hard, low climb-from bar.
    "webarena": 0.15,  # hosted multi-site web agent; very hard, floor bar.
    "agentbench": 0.30,  # 8-environment agent suite; aggregate climb-from bar.
    "agentdojo": 0.60,  # prompt-injection robustness (utility-under-attack); governance bar.
    "aime": 0.30,  # competition math; hard integer-answer reasoning bar.
    "locomo": 0.50,  # long-conversational memory recall (Madras differentiator).
    "clamber": 0.50,  # clarify/ambiguity — Madras clarify-capability axis.
    "gdpval": 0.40,  # real-world deliverable value vs expert (judge-scored).
    "hle": 0.10,  # Humanity's Last Exam — frontier knowledge; very hard, floor bar.
    "livebench": 0.45,  # contamination-resistant reasoning/math.
    "browsecomp": 0.10,  # hard live-web fact-finding; browsing-gated, floor bar.
    "mcpuniverse": 0.40,  # real-MCP-server agent tasks (execution-scored); tool-use climb bar.
    "mcpatlas": 0.40,  # real-MCP-server agent tasks (Scale); tool-use climb bar.
    "mcptoolbench": 0.50,  # multi-category MCP tool-calling; tool-selection/args bar.
    "toolathlon": 0.35,  # hard multi-app MCP agent tasks; climb-from bar.
    "metr": 0.30,  # METR autonomy tasks (Inspect bridge); hard agentic bar.
    "itbench": 0.30,  # incident RCA (graded LLM-judge); SRE reasoning bar.
    "osworld": 0.15,  # computer-use desktop control; very hard, floor bar.
    "embodiedbench": 0.30,  # embodied planning (sim); long-horizon bar.
    "frames": 0.40,  # multi-hop web fact-finding across several pages; gaia-tier climb bar.
    "sealqa": 0.10,  # 2025 hard tier — noisy/conflicting search results; floor bar (hle-tier).
    "memoryagentbench": 0.40,  # 4 memory competencies incl. conflict-resolution; harder than locomo
    "knowedit": 0.45,  # knowledge-update (recent facts); gpqa-tier factual-reasoning bar.
    "agentsafety": 0.90,  # safe-refusal judge-scored; safety-suite tier (agentharm-adjacent).
    "abgcoqa": 0.50,  # clarify-under-ambiguity; same clarify-dimension bar as clamber.
    "injecagent": 0.60,  # ignore an injected tool-result instruction; agentdojo-tier (governance).
    "conflictqa": 0.50,  # hold the correct belief against counter-evidence; moderate reasoning bar.
    "rolebench": 0.55,  # persona/role-voice consistency, judge-scored.
    "personagym": 0.55,  # persona fidelity across PersonaGym's consistency dims; rolebench-tier.
    "compounding": 0.0,  # NOVEL Madras-original moat metric — no external comparable exists;
    # genuinely blank until Run #1 sets the real number (documented policy, not a placeholder gap).
    "mcppoison": 0.0,  # NOVEL Madras-original moat metric — same as compounding, blank until run1.
    "identity_boundary_conformance": 1.0,  # deterministic security invariant, not a climb-from
    # bar — every adversarial+happy-path case MUST pass; any regression below 1.0 is a real defect.
    "routing_resilience_conformance": 1.0,  # same class: routing correctness is an invariant.
    "durable_state_conformance": 1.0,  # same class: park/resume/compaction correctness invariant.
    "compile_conformance": 1.0,  # same class: precedence/provenance/reload correctness invariant.
    "gepa_evolve_conformance": 1.0,  # invariant: evolve() never regresses/auto-applies.
    "memory_sovereignty_conformance": 1.0,  # same class: roundtrip/idempotency/tamper invariant.
    "skill_retrieval_conformance": 1.0,  # same class: retrieval-accuracy correctness invariant.
    "tool_reliability_conformance": 1.0,  # same class: tool-contract/error-handling invariant.
    "arc_agi2": 0.15,  # §12a frontier suite; genuinely hard even for frontier models (52-77%
    # SOTA range), low climb-from bar for scaffold-wrapped mid-tier models. Reported on its own
    # internal panel per BD2, not blended into the Index — this target is still tracked though.
    "swebench_pro": 0.20,  # code-agent gate successor (W2-provisioned, like webvoyager/
    # androidworld); harder than swebench Verified's 0.30 since Pro's held-out design + longer-
    # horizon tasks resist the easier wins Verified allows.
    "marketplace_gate_conformance": 1.0,  # deterministic gate-logic correctness invariant.
    "builder_pipeline_conformance": 1.0,  # deterministic role-naming-collision invariant.
    "tamper_evident_audit_conformance": 1.0,  # deterministic hash-chain correctness invariant.
    "delegation_isolation_conformance": 1.0,  # deterministic depth/budget-isolation invariant.
    "hitl_absorption_conformance": 1.0,  # deterministic answer-absorption correctness invariant.
    "scheduler_reliability_conformance": 1.0,  # deterministic crash-recovery/misfire invariant.
    "verify_pool_robustness_conformance": 1.0,  # deterministic judge-panel-robustness invariant.
    # §12f new suites (s42) — no spec-stated numbers yet; defensible climb-from bars for
    # judge-graded native vertical scenarios (no established external baseline to anchor to).
    "legalbench": 0.45,  # legal-reasoning classification/NLI; LegalBench's own SOTA is modest.
    "marketing_agent": 0.40,  # native judge-graded campaign-strategy scenarios.
    "financebenchmark": 0.40,  # judge-graded finance_qa/business_brief (ERP-independent slice).
    "eqbench_creative": 0.40,  # judge-graded creative writing; subjective, modest climb-from bar.
    "recruiting_agent": 0.40,  # native judge-graded recruiting-strategy scenarios.
    "strategy_agent": 0.40,  # native judge-graded strategy/consulting scenarios.
    "communication_reach": 0.45,  # native judge-graded channel-format/escalation scenarios.
    "creative_media_generation": 0.35,  # vision/audio-judge-graded artifact-quality scenarios.
    "voice_telephony_multilingual": 0.40,  # native judge-graded voice/telephony/multilingual.
    "questclarify": 0.50,  # ask-the-right-missing-variable, don't over-ask; clarify-dimension bar.
    "epmemory": 0.45,  # episodic/chronological memory recall; memoryagentbench-tier.
    "screenspot": 0.30,  # GUI grounding via pixel bbox_hit; hard perceptual-grounding bar.
    "webvoyager": 0.15,  # live multi-site web agent (W2-provisioned); webarena-tier floor.
    "androidworld": 0.20,  # live Android emulator automation (W2-provisioned); above webvoyager.
    "rcaeval": 0.30,  # multi-modal root-cause telemetry diagnosis (W2-provisioned); agentbench-tier
    "openrca": 0.25,  # long-context (~68GB) telemetry RCA (W2-provisioned); harder than rcaeval.
    "marble": 0.30,  # multi-agent orchestration sim (W2-provisioned); agentbench-tier.
    "collab_overcooked": 0.35,  # cooperative-process sim, smaller task set (W2-provisioned).
    "asb": 0.85,  # Agent Security Bench — memory/tool-poisoning attack-defense; safety-suite tier.
    "webwalkerqa": 0.35,  # deep multi-page web navigation + fact-finding (W2-provisioned); harder
    # than frames' single-hop-adjacent Wikipedia set due to the deep-navigation requirement.
}

TARGET_SOURCE: dict[str, str] = {
    "tau2": "spec",
    "bfcl": "spec",
    "agentharm": "default",
    "gaia": "default",
    "gpqa": "default",
    "gsm8k": "default",
    "mmlu_pro": "default",
    "swebench": "default",
    "terminal_bench": "default",
    "longmemeval": "default",
    "appworld": "default",
    "webarena": "default",
    "agentbench": "default",
    "agentdojo": "default",
    "aime": "default",
    "locomo": "default",
    "clamber": "default",
    "gdpval": "default",
    "hle": "default",
    "livebench": "default",
    "browsecomp": "default",
    "mcpuniverse": "default",
    "mcpatlas": "default",
    "mcptoolbench": "default",
    "toolathlon": "default",
    "metr": "default",
    "itbench": "default",
    "osworld": "default",
    "embodiedbench": "default",
    "frames": "default",
    "sealqa": "default",
    "memoryagentbench": "default",
    "knowedit": "default",
    "agentsafety": "default",
    "abgcoqa": "default",
    "injecagent": "default",
    "conflictqa": "default",
    "rolebench": "default",
    "personagym": "default",
    "compounding": "run1",  # novel Madras-original moat metric — no comparable exists.
    "mcppoison": "run1",  # novel Madras-original moat metric — same, blank-until-Run#1 is policy.
    "identity_boundary_conformance": "spec",  # must-pass invariant, stated here — not pending run1.
    "routing_resilience_conformance": "spec",
    "durable_state_conformance": "spec",
    "compile_conformance": "spec",
    "gepa_evolve_conformance": "spec",
    "memory_sovereignty_conformance": "spec",
    "skill_retrieval_conformance": "spec",  # must-pass invariant, stated here — not run1.
    "tool_reliability_conformance": "spec",  # must-pass invariant, stated here — not run1.
    "arc_agi2": "default",
    "swebench_pro": "default",
    "marketplace_gate_conformance": "spec",
    "builder_pipeline_conformance": "spec",
    "tamper_evident_audit_conformance": "spec",
    "delegation_isolation_conformance": "spec",
    "hitl_absorption_conformance": "spec",
    "scheduler_reliability_conformance": "spec",
    "verify_pool_robustness_conformance": "spec",
    "legalbench": "default",
    "marketing_agent": "default",
    "financebenchmark": "default",
    "eqbench_creative": "default",
    "recruiting_agent": "default",
    "strategy_agent": "default",
    "communication_reach": "default",
    "creative_media_generation": "default",
    "voice_telephony_multilingual": "default",
    "questclarify": "default",
    "epmemory": "default",
    "screenspot": "default",
    "webvoyager": "default",
    "androidworld": "default",
    "rcaeval": "default",
    "openrca": "default",
    "marble": "default",
    "collab_overcooked": "default",
    "asb": "default",
    "webwalkerqa": "default",
}


def target_for(benchmark_family: str) -> float | None:
    """Return the beat-ladder target for ``benchmark_family`` (None if unknown)."""
    return BENCHMARK_TARGETS.get(benchmark_family)


def beats(benchmark_family: str, score: float) -> bool | None:
    """Whether ``score`` meets/beats the target (``>=``); None if no target."""
    target = BENCHMARK_TARGETS.get(benchmark_family)
    if target is None:
        return None
    return score >= target
