"""Proving-ground v2 suite registry.

Aggregates every benchmark suite the sweep engine (v2-C) consumes:

- ``tau2``            — τ²-bench, external self-running (isolated subprocess).
- ``terminal_bench``  — terminal-bench, external Docker/tmux tasks (WSL-backed subprocess).
- ``swebench``        — SWE-bench Verified, external Docker eval (WSL); smoke patch-gen.
- ``bfcl``            — Berkeley Function-Calling Leaderboard (vendored slice).
- ``agentharm``       — AgentHarm refusal-safety slice (vendored).
- ``gaia``            — GAIA multi-step reasoning (token-gated; skips cleanly).
- ``gpqa``            — GPQA diamond graduate science MCQ (token-gated; vendored slice).
- ``gsm8k``           — GSM8K grade-school math word problems (public; vendored slice).
- ``mmlu_pro``        — MMLU-Pro broad-knowledge MCQ (public; vendored slice).
- ``longmemeval``     — LongMemEval long-horizon memory recall (token-gated; vendored slice).
- ``appworld``        — AppWorld interactive app-control agent (external; baseline on our proxy).
- ``webarena``        — WebArena hosted multi-site web agent (external; browsergym, infra-gated).
- ``agentbench``      — AgentBench 8-environment agent suite (external; Docker task servers).
- ``madras_features`` — native Madras scenario bank (the committed scenarios dir).
- ``identity_boundary_conformance`` — s33 Identity & Privilege Boundary, deterministic adversarial
  conformance (external, zero-LLM; framework-10x Part C, C1).
- ``routing_resilience_conformance`` — s33 Zero-cost Routing resilience, deterministic adversarial
  conformance (external, zero-LLM; framework-10x Part C, C2).
- ``durable_state_conformance`` — s33 Durable-state / resumption, deterministic adversarial
  conformance (external, zero-LLM; framework-10x Part C, C3).
- ``compile_conformance`` — s33 Compile pipeline, deterministic adversarial conformance
  (external, zero-LLM; framework-10x Part C, C4).
- ``memory_sovereignty_conformance`` — s33 Memory sovereignty, deterministic adversarial
  conformance (external, zero-LLM; framework-10x Part C, C5).

Each entry is a ready-to-use ``Suite`` instance exposing ``features``/``tools``
coverage metadata. Use ``load_suite(name)`` for one or ``all_suites()`` for all.
"""

from __future__ import annotations

from pathlib import Path

from madras.eval_.proving_ground.suite import NativeSuite, Suite
from madras.eval_.proving_ground.suites.abgcoqa import AbgCoqaSuite
from madras.eval_.proving_ground.suites.agentbench import AgentBenchSuite
from madras.eval_.proving_ground.suites.agentdojo import AgentDojoSuite
from madras.eval_.proving_ground.suites.agentharm import AgentHarmSuite
from madras.eval_.proving_ground.suites.agentsafety import AgentSafetySuite
from madras.eval_.proving_ground.suites.aime import AimeSuite
from madras.eval_.proving_ground.suites.appworld import AppWorldSuite
from madras.eval_.proving_ground.suites.arc_agi2 import ArcAgi2Suite
from madras.eval_.proving_ground.suites.bfcl import BfclSuite
from madras.eval_.proving_ground.suites.browsecomp import BrowseCompSuite
from madras.eval_.proving_ground.suites.builder_pipeline_conformance import (
    BuilderPipelineConformanceSuite,
)
from madras.eval_.proving_ground.suites.clamber import ClamberSuite
from madras.eval_.proving_ground.suites.compile_conformance import CompileConformanceSuite
from madras.eval_.proving_ground.suites.compounding import CompoundingSuite
from madras.eval_.proving_ground.suites.conflictqa import ConflictQaSuite
from madras.eval_.proving_ground.suites.delegation_isolation_conformance import (
    DelegationIsolationConformanceSuite,
)
from madras.eval_.proving_ground.suites.durable_state import DurableStateConformanceSuite
from madras.eval_.proving_ground.suites.embodiedbench import EmbodiedBenchSuite
from madras.eval_.proving_ground.suites.env_harnesses import ENV_HARNESS_SUITES
from madras.eval_.proving_ground.suites.epmemory import EpMemorySuite
from madras.eval_.proving_ground.suites.eqbench_creative import EqBenchCreativeSuite
from madras.eval_.proving_ground.suites.financebenchmark import FinanceBenchmarkSuite
from madras.eval_.proving_ground.suites.frames import FramesSuite
from madras.eval_.proving_ground.suites.gaia import GaiaSuite
from madras.eval_.proving_ground.suites.gdpval import GdpvalSuite
from madras.eval_.proving_ground.suites.gepa_evolve_conformance import (
    GepaEvolveConformanceSuite,
)
from madras.eval_.proving_ground.suites.gpqa import GpqaSuite
from madras.eval_.proving_ground.suites.gsm8k import Gsm8kSuite
from madras.eval_.proving_ground.suites.hitl_absorption_conformance import (
    HitlAbsorptionConformanceSuite,
)
from madras.eval_.proving_ground.suites.hle import HleSuite
from madras.eval_.proving_ground.suites.identity_boundary import IdentityBoundaryConformanceSuite
from madras.eval_.proving_ground.suites.injecagent import InjecAgentSuite
from madras.eval_.proving_ground.suites.itbench import ItBenchSuite
from madras.eval_.proving_ground.suites.knowedit import KnowEditSuite
from madras.eval_.proving_ground.suites.legalbench import LegalBenchSuite
from madras.eval_.proving_ground.suites.livebench import LiveBenchSuite
from madras.eval_.proving_ground.suites.locomo import LoCoMoSuite
from madras.eval_.proving_ground.suites.longmemeval import LongMemEvalSuite
from madras.eval_.proving_ground.suites.marketplace_gate_conformance import (
    MarketplaceGateConformanceSuite,
)
from madras.eval_.proving_ground.suites.mcpatlas import McpAtlasSuite
from madras.eval_.proving_ground.suites.mcppoison import McpPoisonSuite
from madras.eval_.proving_ground.suites.mcptoolbench import McpToolBenchSuite
from madras.eval_.proving_ground.suites.mcpuniverse import McpUniverseSuite
from madras.eval_.proving_ground.suites.memory_sovereignty import MemorySovereigntyConformanceSuite
from madras.eval_.proving_ground.suites.memoryagentbench import MemoryAgentBenchSuite
from madras.eval_.proving_ground.suites.metr import MetrSuite
from madras.eval_.proving_ground.suites.mmlu_pro import MmluProSuite
from madras.eval_.proving_ground.suites.osworld import OsWorldSuite
from madras.eval_.proving_ground.suites.personagym import PersonaGymSuite
from madras.eval_.proving_ground.suites.questclarify import QuestClarifySuite
from madras.eval_.proving_ground.suites.rolebench import RoleBenchSuite
from madras.eval_.proving_ground.suites.routing_resilience import RoutingResilienceConformanceSuite
from madras.eval_.proving_ground.suites.scheduler_reliability_conformance import (
    SchedulerReliabilityConformanceSuite,
)
from madras.eval_.proving_ground.suites.screenspot import ScreenSpotSuite
from madras.eval_.proving_ground.suites.sealqa import SealQaSuite
from madras.eval_.proving_ground.suites.skill_retrieval_conformance import (
    SkillRetrievalConformanceSuite,
)
from madras.eval_.proving_ground.suites.swebench import SweBenchSuite
from madras.eval_.proving_ground.suites.swebench_pro import SweBenchProSuite
from madras.eval_.proving_ground.suites.tamper_evident_audit_conformance import (
    TamperEvidentAuditConformanceSuite,
)
from madras.eval_.proving_ground.suites.tau2 import Tau2Suite
from madras.eval_.proving_ground.suites.terminal_bench import TerminalBenchSuite
from madras.eval_.proving_ground.suites.tool_reliability_conformance import (
    ToolReliabilityConformanceSuite,
)
from madras.eval_.proving_ground.suites.toolathlon import ToolathlonSuite
from madras.eval_.proving_ground.suites.verify_pool_robustness_conformance import (
    VerifyPoolRobustnessConformanceSuite,
)
from madras.eval_.proving_ground.suites.webarena import WebArenaSuite

_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios"
_MARKETING_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios_marketing"
_RECRUITING_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios_recruiting"
_REACH_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios_reach"
_STRATEGY_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios_strategy"
_CREATIVE_MEDIA_SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "scenarios_creative_media"
_VOICE_TELEPHONY_SCENARIOS_DIR = (
    Path(__file__).resolve().parent.parent / "scenarios_voice_telephony"
)

SUITES: dict[str, Suite] = {
    "tau2": Tau2Suite(),
    "tamper_evident_audit_conformance": TamperEvidentAuditConformanceSuite(),
    "terminal_bench": TerminalBenchSuite(),
    "bfcl": BfclSuite(),
    "builder_pipeline_conformance": BuilderPipelineConformanceSuite(),
    "agentharm": AgentHarmSuite(),
    "gaia": GaiaSuite(),
    "gpqa": GpqaSuite(),
    "gsm8k": Gsm8kSuite(),
    "mmlu_pro": MmluProSuite(),
    "longmemeval": LongMemEvalSuite(),
    "swebench": SweBenchSuite(),
    "swebench_pro": SweBenchProSuite(),
    "appworld": AppWorldSuite(),
    "arc_agi2": ArcAgi2Suite(),
    "webarena": WebArenaSuite(),
    "agentbench": AgentBenchSuite(),
    "agentdojo": AgentDojoSuite(),
    "aime": AimeSuite(),
    "locomo": LoCoMoSuite(),
    "clamber": ClamberSuite(),
    "gdpval": GdpvalSuite(),
    "hle": HleSuite(),
    "hitl_absorption_conformance": HitlAbsorptionConformanceSuite(),
    "livebench": LiveBenchSuite(),
    "browsecomp": BrowseCompSuite(),
    "mcpuniverse": McpUniverseSuite(),
    "marketplace_gate_conformance": MarketplaceGateConformanceSuite(),
    "mcpatlas": McpAtlasSuite(),
    "mcptoolbench": McpToolBenchSuite(),
    "toolathlon": ToolathlonSuite(),
    "verify_pool_robustness_conformance": VerifyPoolRobustnessConformanceSuite(),
    "metr": MetrSuite(),
    "itbench": ItBenchSuite(),
    "osworld": OsWorldSuite(),
    "embodiedbench": EmbodiedBenchSuite(),
    "frames": FramesSuite(),
    "sealqa": SealQaSuite(),
    "memoryagentbench": MemoryAgentBenchSuite(),
    "knowedit": KnowEditSuite(),
    "legalbench": LegalBenchSuite(),
    "agentsafety": AgentSafetySuite(),
    "abgcoqa": AbgCoqaSuite(),
    "injecagent": InjecAgentSuite(),
    "conflictqa": ConflictQaSuite(),
    "rolebench": RoleBenchSuite(),
    "personagym": PersonaGymSuite(),
    "compounding": CompoundingSuite(),
    "delegation_isolation_conformance": DelegationIsolationConformanceSuite(),
    "mcppoison": McpPoisonSuite(),
    "identity_boundary_conformance": IdentityBoundaryConformanceSuite(),
    "routing_resilience_conformance": RoutingResilienceConformanceSuite(),
    "durable_state_conformance": DurableStateConformanceSuite(),
    "compile_conformance": CompileConformanceSuite(),
    "gepa_evolve_conformance": GepaEvolveConformanceSuite(),
    "memory_sovereignty_conformance": MemorySovereigntyConformanceSuite(),
    "skill_retrieval_conformance": SkillRetrievalConformanceSuite(),
    "tool_reliability_conformance": ToolReliabilityConformanceSuite(),
    "questclarify": QuestClarifySuite(),
    "epmemory": EpMemorySuite(),
    "eqbench_creative": EqBenchCreativeSuite(),
    "financebenchmark": FinanceBenchmarkSuite(),
    "screenspot": ScreenSpotSuite(),
    "scheduler_reliability_conformance": SchedulerReliabilityConformanceSuite(),
    **ENV_HARNESS_SUITES,
    "madras_features": NativeSuite(
        id="madras_features",
        name="Madras native feature scenarios",
        version="v2",
        kind="native",
        provenance="Madras-authored scenario bank (proving_ground/scenarios)",
        directory=str(_SCENARIOS_DIR),
    ),
    "marketing_agent": NativeSuite(
        id="marketing_agent",
        name="Marketing-agent scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored (benchmark-design.md §12f) — no adoptable open "
            "marketing-agent benchmark exists (AD-Bench is proprietary/unreleased, "
            "verified s42); fills the Maverick (Marketing, vs NoimosAI/Albert.ai/"
            "Agentforce/HubSpot Breeze) vertical gap with judge-rubric-graded campaign-"
            "strategy scenarios."
        ),
        directory=str(_MARKETING_SCENARIOS_DIR),
    ),
    "recruiting_agent": NativeSuite(
        id="recruiting_agent",
        name="Recruiting-agent scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored (benchmark-design.md §12f) — no adoptable end-to-end "
            "recruiting-agent benchmark exists (raw resume/JD classification datasets "
            "exist but lack the pairing needed for agent evaluation, per s42 research); "
            "fills the Joy (Recruiting, vs GoPerfect/hireEZ/SeekOut/Eightfold) vertical "
            "gap with judge-rubric-graded scenarios."
        ),
        directory=str(_RECRUITING_SCENARIOS_DIR),
    ),
    "communication_reach": NativeSuite(
        id="communication_reach",
        name="Communication & Reach scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored (benchmark-design.md §12f) — fills the Communication & "
            "Reach capability gap (9 capabilities, previously untested: multi-channel "
            "deploy/reach quality — format adaptation, escalation routing, webhook "
            "robustness, tone consistency, delivery-failure fallback). Grounded in the "
            "real messaging/apprise_sender.py channel-dispatch module."
        ),
        directory=str(_REACH_SCENARIOS_DIR),
    ),
    "creative_media_generation": NativeSuite(
        id="creative_media_generation",
        name="Creative & Media generation scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored — fills the Creative & Media capability gap (Image "
            "Generation, Media Pipeline, Music Generation) confirmed still open after "
            "the s42 audit corrected an earlier mis-attribution (eqbench_creative tests "
            "creative WRITING, not media-artifact generation). Vision/audio-capable-"
            "judge-graded: exercises the real image_generate/media_pipeline/music "
            "generation tools and scores the actual produced artifact."
        ),
        directory=str(_CREATIVE_MEDIA_SCENARIOS_DIR),
    ),
    "strategy_agent": NativeSuite(
        id="strategy_agent",
        name="Strategy-agent scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored — no adoptable open strategy/management-consulting-agent "
            "benchmark exists (real consulting engagements aren't published with ground "
            "truth, matching the same proprietary-domain finding already confirmed for "
            "Marketing/Recruiting, s42). Fills the Sage (Strategy/consulting, vs "
            "mgmt-consultant AI) vertical gap — the last of the 9 launch-roster verticals "
            "without suite coverage. Judge-rubric-graded: problem-framing-before-"
            "recommending, competitor-gap-finding, go/no-go decision framework, quarterly "
            "plan-vs-actual review, and Sage's own stated moat (compounding strategic "
            "memory recall across engagements)."
        ),
        directory=str(_STRATEGY_SCENARIOS_DIR),
    ),
    "voice_telephony_multilingual": NativeSuite(
        id="voice_telephony_multilingual",
        name="Voice/Telephony/Multilingual scenarios (native)",
        version="v1",
        kind="native",
        provenance=(
            "Madras-authored — fills the remaining 8 of 9 Communication & Reach "
            "capabilities left untested after communication_reach only covered "
            "Messaging & Reach (s42 audit): Voice Input (ASR), Voice Output (TTS), "
            "Telephony-IVR, Indic-Multilingual, Human Handoff, Inbound Channels, "
            "Session Sharing, Offline Speech."
        ),
        directory=str(_VOICE_TELEPHONY_SCENARIOS_DIR),
    ),
}


def load_suite(name: str) -> Suite:
    """Return the registered suite for ``name`` (raises ``KeyError`` if unknown)."""
    return SUITES[name]


def all_suites() -> list[Suite]:
    """Return every registered suite."""
    return list(SUITES.values())


__all__ = [
    "ENV_HARNESS_SUITES",
    "SUITES",
    "AbgCoqaSuite",
    "AgentBenchSuite",
    "AgentHarmSuite",
    "AgentSafetySuite",
    "AppWorldSuite",
    "ArcAgi2Suite",
    "BfclSuite",
    "BuilderPipelineConformanceSuite",
    "CompoundingSuite",
    "ConflictQaSuite",
    "DelegationIsolationConformanceSuite",
    "EmbodiedBenchSuite",
    "EpMemorySuite",
    "EqBenchCreativeSuite",
    "FinanceBenchmarkSuite",
    "FramesSuite",
    "GaiaSuite",
    "GepaEvolveConformanceSuite",
    "GpqaSuite",
    "Gsm8kSuite",
    "HitlAbsorptionConformanceSuite",
    "InjecAgentSuite",
    "ItBenchSuite",
    "KnowEditSuite",
    "LegalBenchSuite",
    "LongMemEvalSuite",
    "MarketplaceGateConformanceSuite",
    "McpAtlasSuite",
    "McpPoisonSuite",
    "McpToolBenchSuite",
    "McpUniverseSuite",
    "MemoryAgentBenchSuite",
    "MetrSuite",
    "MmluProSuite",
    "NativeSuite",
    "OsWorldSuite",
    "PersonaGymSuite",
    "QuestClarifySuite",
    "RoleBenchSuite",
    "SchedulerReliabilityConformanceSuite",
    "ScreenSpotSuite",
    "SealQaSuite",
    "SkillRetrievalConformanceSuite",
    "SweBenchProSuite",
    "SweBenchSuite",
    "TamperEvidentAuditConformanceSuite",
    "Tau2Suite",
    "TerminalBenchSuite",
    "ToolReliabilityConformanceSuite",
    "ToolathlonSuite",
    "VerifyPoolRobustnessConformanceSuite",
    "WebArenaSuite",
    "all_suites",
    "load_suite",
]
