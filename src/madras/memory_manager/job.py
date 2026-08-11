"""Memory Manager nightly job — consolidate episodes, promote reflexes, generate briefing."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from madras.llm.gateway import LLMGateway
from madras.memory.episodic import EpisodicMemory
from madras.memory.reflex import ReflexMemory
from madras.memory.tenant_context import tenant_scope
from madras.memory_manager.consolidator import consolidate
from madras.memory_manager.plan_reconciler import reconcile_plans
from madras.memory_manager.reflex_extractor import extract_candidates, promote
from madras.mindpalace.briefing import BriefingGenerator
from madras.mindpalace.ledger import MindPalaceLedger
from madras.mindpalace.plan_ledger import PlanLedger


def default_file_memory_root() -> Any:
    """`<workspace>/memory` — same workspace-root resolution as tools/sandbox.py's
    _workspace_root(), so quick-add files and the sandbox's own workspace share one root.

    H8 (tamil-and-backend-spatial): renamed public -- delegate.py's
    _get_calibration_tracker() genuinely needs this cross-module (pyright's
    reportPrivateUsage was correctly flagging a boundary violation, not a typing gap)."""
    from pathlib import Path

    from madras.config import settings

    base = (
        Path(settings.madras_workspace)
        if settings.madras_workspace
        else Path(__file__).resolve().parents[3] / "workspace"
    )
    return base / "memory"


def _default_learning_world() -> Any:
    """`<workspace>/learning_history`, FileWorld-backed (row 87 DurableWorld) -- survives a
    restart, no Postgres needed for this small rolling counter series."""
    from madras.tasks.durable_world import FileWorld

    root = default_file_memory_root().parent / "learning_history"
    return FileWorld(root=str(root))


@dataclass
class NightlyReport:
    sessions_seen: int
    episodes_written: int
    reflexes_promoted: int
    briefing_id: int | None
    plans_done: int = 0
    plans_drift: int = 0
    canon_briefing: dict[str, Any] | None = field(default=None)
    principles_written: int = 0
    memory_briefing: dict[str, Any] | None = field(default=None)
    learned_context_written: int = 0  # B3 sleep-time distilled blocks
    turns_consolidated: int = 0  # W1·c 3c — per-turn logs distilled into the Fabric
    turn_memories_written: int = 0
    skills_principled: int = 0  # W1 ② — active skills distilled into principles
    file_memory_imported: int = 0  # row 14f — quick-add .md files reconciled into the Fabric
    stagnation_detected: bool = False  # row learning-engine — the agent stopped learning
    momentum_streak: int = 0  # row health-manager — consecutive improving nights


class MemoryManagerJob:
    """Orchestrates the nightly memory-manager pipeline."""

    def __init__(
        self,
        *,
        ledger: MindPalaceLedger,
        episodic: EpisodicMemory,
        reflex: ReflexMemory,
        gateway: LLMGateway,
        plan_ledger: PlanLedger | None = None,
        canon_ledger: Any = None,
        fabric: Any = None,
        session_index: Any = None,
        turn_ledger: Any = None,
        skill_store: Any = None,
        file_memory_root: str | None = None,
        learning_world: Any = None,  # DurableWorld | None; row learning-engine's history store
    ) -> None:
        self._ledger = ledger
        self._episodic = episodic
        self._reflex = reflex
        self._gateway = gateway
        self._plan_ledger = plan_ledger
        self._canon_ledger = canon_ledger
        self._fabric = fabric
        self._session_index = session_index
        self._turn_ledger = turn_ledger
        self._skill_store = skill_store
        self._file_memory_root = file_memory_root
        self._learning_world = learning_world

    async def run(
        self,
        *,
        tenant: str,
        project: str = "default",
        agent_name: str = "shadow",
        target_date: date,
        lookback: int = 50,
        now: float | None = None,
    ) -> NightlyReport:
        """Run the nightly pipeline FOR ONE PERSON and return a summary report.

        s63: `tenant` is required and has no default. The job runs overnight with nobody at the
        door, so it is the caller most likely to reach the memory layer unidentified -- and the
        memory layer now refuses to write a memory owned by no one. A scheduler wanting to serve
        several people calls this once per person, which also means one person's failure cannot
        abort everybody else's consolidation.

        A thin wrapper on purpose: binding the badge here rather than re-indenting the whole
        pipeline body keeps this change small enough to read, and guarantees every step below --
        present and future -- runs inside the scope without needing to know it exists.
        """
        with tenant_scope(tenant):
            return await self._run(
                project=project,
                agent_name=agent_name,
                target_date=target_date,
                lookback=lookback,
                now=now,
            )

    async def _run(
        self,
        *,
        project: str = "default",
        agent_name: str = "shadow",
        target_date: date,
        lookback: int = 50,
        now: float | None = None,
    ) -> NightlyReport:
        """The pipeline itself. Always called inside a bound tenant scope -- see `run`."""
        sessions = await self._ledger.recent(project=project, agent_name=agent_name, limit=lookback)

        await self._episodic.setup()
        episodes_written = await consolidate(sessions, episodic=self._episodic)

        # Index sessions into the search vector half (semantic session recall).
        if self._session_index is not None:
            from madras.mindpalace.session_search import SessionSearch

            ss = SessionSearch(self._ledger, vector_index=self._session_index)
            for rec in sessions:
                try:
                    await ss.index_session(rec)
                except Exception:
                    pass

        cands = extract_candidates(sessions)
        reflexes_promoted = await promote(cands, agent_name=agent_name, reflex=self._reflex)

        plans_done = 0
        plans_drift = 0
        if self._plan_ledger is not None:
            reconcile = await reconcile_plans(
                plan_ledger=self._plan_ledger,
                sessions=sessions,
                agent_name=agent_name,
                project=project,
            )
            plans_done = reconcile.items_confirmed_done
            plans_drift = reconcile.items_flagged_drift

        # Canon enforcement is not part of this repo -- see the note on the removed
        # import above. The field stays so the report shape is unchanged.
        canon_briefing: dict[str, Any] | None = None

        # L5 reflection — distil durable principles from the fabric's memories.
        principles_written = 0
        if self._fabric is not None:
            import time as _time

            from madras.memory_manager.reflection_job import reflect

            try:
                r = await reflect(self._fabric, now=now or _time.time(), agent_name=agent_name)
                principles_written = r["principles_written"]
            except Exception:
                principles_written = 0

        # W1 ② — skills -> principles: each active skill becomes a durable principle.
        skills_principled = 0
        if self._fabric is not None and self._skill_store is not None:
            import time as _t5

            from madras.memory_manager.reflection_job import reflect_skills

            try:
                rs = await reflect_skills(
                    self._fabric, self._skill_store, now=now or _t5.time(), agent_name=agent_name
                )
                skills_principled = rs["principles_written"]
            except Exception:
                skills_principled = 0

        # Memory-health enforcement — decay-with-dignity + contradiction/provenance audit.
        memory_briefing: dict[str, Any] | None = None
        if self._fabric is not None:
            import time as _t2

            from madras.memory_manager.memory_enforcement import enforce_memory

            try:
                memory_briefing = await enforce_memory(self._fabric, now=now or _t2.time())
            except Exception:
                memory_briefing = None

        # B3 sleep-time pass — distil recent raw memories into a shareable learned-context
        # block (built on top of this nightly agent; raw -> learned context, Letta-style).
        learned_context_written = 0
        if self._fabric is not None:
            import time as _t3

            from madras.sleeptime import distill_learned_context

            try:
                _now = now or _t3.time()
                items = await self._fabric.current_items(now=_now)
                lc = distill_learned_context(items, now=_now, agent=agent_name)
                if lc is not None:
                    await self._fabric.remember(lc, now=_now)
                    learned_context_written = 1
            except Exception:
                learned_context_written = 0

        # W1·c 3c — distil the detailed per-turn logs into atomic Fabric memories.
        turns_consolidated = 0
        turn_memories_written = 0
        if self._turn_ledger is not None and self._fabric is not None:
            import time as _t4

            from madras.memory_manager.turn_consolidator import consolidate_turns

            try:
                turns_consolidated, turn_memories_written = await consolidate_turns(
                    self._turn_ledger, self._fabric, agent_name=agent_name, now=now or _t4.time()
                )
            except Exception:
                turns_consolidated = turn_memories_written = 0

        # row 14f — file-memory quick-add: reconcile hand-/quick-added .md files (written by
        # memory/quick_add.py's capture_quick_adds, itself wired into every real turn) back
        # through MemoryFabric.remember, so contradiction arbitration + provenance apply.
        # import_from_files() had no live caller before this.
        file_memory_imported = 0
        if self._fabric is not None:
            import time as _t6

            from madras.memory.file_memory import FileMemoryStore, import_from_files

            try:
                root = self._file_memory_root or str(default_file_memory_root())
                store = FileMemoryStore(root=root, agent_name=agent_name)
                # import_from_files() returns ids EXPIRED by contradiction, not ids written
                # (mirrors MemoryFabric.remember()'s own contract) -- count files processed.
                file_memory_imported = len(store.import_all())
                await import_from_files(self._fabric, store, now=now or _t6.time())
            except Exception:
                file_memory_imported = 0

        # row learning-engine — Learning Engine's own gap: "plateau/stagnation detector
        # (no such file — genuinely absent)". The standard patience-based algorithm
        # (PyTorch ReduceLROnPlateau / Keras EarlyStopping) applied to tonight's real
        # learning signal (reflexes + principles + skills distilled) -- if nothing new
        # got learned for `patience` consecutive nights despite sessions still happening,
        # that's a genuine plateau, not a benchmark-only metric.
        stagnation_detected = False
        momentum_streak = 0
        learning_signal = reflexes_promoted + principles_written + skills_principled
        try:
            from madras.metacog.momentum import MomentumTracker
            from madras.metacog.stagnation import LearningHistory, StagnationDetector

            world = self._learning_world or _default_learning_world()
            history = LearningHistory(world=world).append(agent_name, float(learning_signal))
            stagnation_detected = StagnationDetector().check(history).plateaued
            # row health-manager — momentum is the literal inverse read of the SAME
            # series: consecutive IMPROVING nights, not consecutive non-improving ones.
            momentum_streak = MomentumTracker().check(history).streak
        except Exception:
            stagnation_detected = False
            momentum_streak = 0

        briefing_id: int | None = None
        if sessions:
            briefing_id = await BriefingGenerator(
                gateway=self._gateway, ledger=self._ledger
            ).generate(project=project, agent_name=agent_name, target_date=target_date)

        return NightlyReport(
            principles_written=principles_written,
            memory_briefing=memory_briefing,
            sessions_seen=len(sessions),
            episodes_written=episodes_written,
            reflexes_promoted=reflexes_promoted,
            briefing_id=briefing_id,
            plans_done=plans_done,
            plans_drift=plans_drift,
            canon_briefing=canon_briefing,
            learned_context_written=learned_context_written,
            turns_consolidated=turns_consolidated,
            turn_memories_written=turn_memories_written,
            skills_principled=skills_principled,
            file_memory_imported=file_memory_imported,
            stagnation_detected=stagnation_detected,
            momentum_streak=momentum_streak,
        )
