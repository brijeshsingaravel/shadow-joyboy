"""compiler/optimize.py — GEPA optimize-until-pass loop (E1 Task C2).

Diagnose-then-route (current self-improvement research: separate "software problems"
(the harness/config) from "knowledge problems" (the text) before picking a fix track),
derived from C1's OWN deterministic semantics, not a fuzzy classifier:
  - zero eligible scenarios (VerifyResult.per_gate == {}) is unambiguously a
    CAPABILITY gap -- the agent has nothing it can even attempt. Fixed deterministically
    (add one tier-entitled capability), no LLM call needed.
  - a non-empty per_gate with failures is unambiguously a QUALITY gap -- the agent had
    the right tools but underperformed. Routed to the REAL optimizer.evolve() GEPA loop
    (upstream gepa's own MCP Adapter precedent confirms tool/capability changes and text
    evolution are properly separate optimization tracks, not one mechanism).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from madras_capabilities.catalog import Catalog
from madras_capabilities.tiers import plan_entitlement_policy

from madras.compiler.compile import compile_agent
from madras.compiler.emit import emit_role
from madras.compiler.verify import verify_agent
from madras.factory.dynamic import AuthContext
from madras.factory.spawn import AgentRecord, spawn_agent
from madras.llm.gateway import LLMGateway, LLMRequest
from madras.models.agent_spec import AgentSpec
from madras.optimizer.evolve import evolve
from madras.optimizer.models import OptimProposal, Target


@dataclass
class ResidencyResult:
    verified: bool
    rounds: int
    lift: float
    record: AgentRecord | None = None
    # row self-optimization-engine — the previously-dead OptimProposal.approved gate,
    # made real: with auto_approve=False, an improved proposal is collected here
    # instead of silently applied (score-lift alone is a known-insufficient gate).
    pending_proposals: list[OptimProposal] = field(default_factory=list[OptimProposal])


def _spec_from_record(record: AgentRecord, outcome: str) -> AgentSpec:
    config = record.config
    persona = config.persona
    return AgentSpec(
        outcome=outcome,
        name=config.name,
        archetype=config.archetype,
        neighborhood=config.neighborhood,
        persona_voice=persona.voice if persona else "",
        persona_refusal_style=persona.refusal_style if persona else "",
        persona_north_star=persona.north_star if persona else "",
        discovery_summary=config.capability_summary or outcome,
        capabilities=list(config.capabilities),
        skills=[s.name for s in config.skills] if config.skills else [],
        execution=config.execution.default_pattern.value if config.execution else "react",
    )


def _write_and_spawn(agents_dir: Path, spec: AgentSpec) -> AgentRecord:
    # Overwrites the SAME in-progress compiled agent across optimization rounds -- not
    # a new agent, so no collision check (that already happened at round 0's
    # compile_agent() call). Writes to compiled/, never roles/ (read-only in the
    # live container; E1 Task E2 finding).
    compiled_dir = Path(agents_dir) / "compiled"
    compiled_dir.mkdir(parents=True, exist_ok=True)
    role_path = compiled_dir / f"{spec.name}.yaml"
    role_path.write_text(yaml.safe_dump(emit_role(spec)), encoding="utf-8")
    return spawn_agent(agents_dir=agents_dir, role_name=spec.name)


async def _reflect_on_persona(
    gateway: LLMGateway,
    model: str,
    current_text: str,
    failures: list[str],
) -> str:
    prompt = (
        "This agent's persona_voice didn't perform well on real tasks.\n"
        f"Current persona_voice: {current_text!r}\n"
        f"Failed on: {failures or 'general underperformance'}\n\n"
        "Propose an improved persona_voice (one sentence). Reply with ONLY the new "
        "persona_voice text, nothing else."
    )
    req = LLMRequest(model=model, messages=[{"role": "user", "content": prompt}])
    resp = await gateway.complete(req)
    return resp.text.strip()


async def compile_to_residency(
    *,
    outcome: str,
    gateway: LLMGateway,
    model: str,
    agents_dir: Path,
    catalog: Catalog,
    auth: AuthContext,
    budget: int = 2,
    auto_approve: bool = True,
) -> ResidencyResult:
    """``auto_approve=True`` (default) preserves the tested compile-time behavior
    exactly: an improved GEPA proposal is applied immediately, then re-verified within
    this same bounded, budget-capped loop -- the synchronous re-verify is its own
    safety net. ``auto_approve=False`` is for calling ``evolve()`` OUTSIDE that
    closed loop (a resident/deployed agent's live text, from production traces) where
    there is no synchronous re-verify net and score-lift alone is a known-insufficient
    gate (row self-optimization-engine) -- an improved proposal is collected into
    ``pending_proposals`` instead of applied, and the round ends there."""
    compile_result = await compile_agent(
        outcome=outcome,
        gateway=gateway,
        model=model,
        agents_dir=agents_dir,
        catalog=catalog,
        auth=auth,
    )
    if compile_result.mode == "clarify" or compile_result.agent is None:
        return ResidencyResult(verified=False, rounds=0, lift=0.0, record=None)

    record = compile_result.agent
    verify_result = await verify_agent(record, gateway, model)
    if verify_result.passed:
        return ResidencyResult(verified=True, rounds=0, lift=0.0, record=record)

    baseline_index = verify_result.index
    spec = _spec_from_record(record, outcome)
    pending: list[OptimProposal] = []

    for round_i in range(1, budget + 1):
        if not verify_result.per_gate:
            # Capability gap -- deterministic, no LLM call. Must prefer a capability
            # that actually resolves to a non-empty toolset (structural capabilities
            # like acp-surface have implements: [] and would never fix a zero-toolset
            # gap -- a real bug caught by the live-drive test, not assumed away).
            entitled = plan_entitlement_policy(catalog)(auth)
            missing = sorted(entitled - set(spec.capabilities))
            tool_backed = [cap_id for cap_id in missing if catalog.by_id[cap_id].implements]
            pick = tool_backed or missing
            if pick:
                spec.capabilities = [*spec.capabilities, pick[0]]
        else:
            # Quality gap -- the real GEPA loop.
            failing_ids = list(verify_result.failures)

            async def _evaluate(text: str) -> dict[str, float]:
                candidate = spec.model_copy(update={"persona_voice": text})
                candidate_record = _write_and_spawn(agents_dir, candidate)
                vr = await verify_agent(candidate_record, gateway, model)
                return dict(vr.per_gate)

            async def _reflect(
                text: str, _failures: list[str], _fids: list[str] = failing_ids
            ) -> str:
                return await _reflect_on_persona(gateway, model, text, _fids)

            target = Target(kind="prompt", id=spec.name, current_text=spec.persona_voice)
            proposal = await evolve(target, evaluate=_evaluate, reflect=_reflect, rounds=1)
            if proposal.improved:
                if auto_approve:
                    spec.persona_voice = proposal.new_text
                else:
                    pending.append(proposal)
                    return ResidencyResult(
                        verified=False,
                        rounds=round_i,
                        lift=verify_result.index - baseline_index,
                        record=record,
                        pending_proposals=pending,
                    )

        record = _write_and_spawn(agents_dir, spec)
        verify_result = await verify_agent(record, gateway, model)
        if verify_result.passed:
            return ResidencyResult(
                verified=True,
                rounds=round_i,
                lift=verify_result.index - baseline_index,
                record=record,
                pending_proposals=pending,
            )

    return ResidencyResult(
        verified=False,
        rounds=budget,
        lift=verify_result.index - baseline_index,
        record=record,
        pending_proposals=pending,
    )
