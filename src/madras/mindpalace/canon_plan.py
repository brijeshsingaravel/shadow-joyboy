"""Canon Plan — the durable, vision-anchored user plan (the planning moat).

One row per project canon: a Vision/north-star with nested Phases → Tasks, plus
Pivots (scope expansions appended, never overwritten) and an append-only Revision
history (git-for-vision: diff / blame / rollback). Tables: 0010_canon_plan.sql.
Mirrors PlanLedger (0004); the per-task agent layer remains the existing PlanLedger.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, cast

import asyncpg


@dataclass
class CanonTask:
    id: str
    title: str
    status: str = "pending"  # pending | in_progress | done | blocked
    woop: dict[str, str] = field(default_factory=dict[str, str])  # wish/outcome/obstacle/plan
    evidence: list[str] = field(default_factory=list[str])
    blocked_by: list[str] = field(default_factory=list[str])
    order: int = 0
    # Prioritization metadata (W2·G2) — drive the prioritize.* Analyst lenses.
    priority: str = ""  # MoSCoW band: must | should | could | wont (or "")
    urgent: bool = False  # Eisenhower urgency axis (importance ~ priority band)
    impact: int = 0  # relative value 1-5 (RICE/WSJF/Pareto); 0 = unscored
    effort: int = 0  # relative effort 1-5 (RICE/WSJF); 0 = unscored


@dataclass
class CanonPhase:
    id: str
    title: str
    objective: str = ""
    key_results: list[str] = field(default_factory=list[str])
    status: str = "pending"  # pending | active | done
    order: int = 0
    # E-X3 spec-driven-development triad: requirements -> design -> tasks (user-sovereign).
    requirements: list[str] = field(default_factory=list[str])  # acceptance criteria / user stories
    design: str = ""  # the approach / design note
    tasks: list[CanonTask] = field(default_factory=list[CanonTask])


@dataclass
class CanonPivot:
    id: str
    when: str
    reason: str
    impact: str = ""
    phase_id: str | None = None
    source: str = "user"  # user | agent
    disposition: str = "open"  # open | absorbed | deferred | rejected


@dataclass
class CanonIdea:
    """An idea or deferred item — the user's parking lot, so nothing is lost."""

    id: str
    title: str
    category: str = ""
    status: str = "idea"  # idea | deferred | building | done | rejected
    note: str = ""
    order: int = 0


@dataclass
class Canon:
    plan_id: str
    project: str = "default"
    vision: str = ""
    north_star: str = ""
    version: int = 1
    target_date: str = ""  # launch driver, e.g. "2026-09-18" (the date that pulls everything)
    phases: list[CanonPhase] = field(default_factory=list[CanonPhase])
    pivots: list[CanonPivot] = field(default_factory=list[CanonPivot])
    ideas: list[CanonIdea] = field(default_factory=list[CanonIdea])


_UPSERT = """
INSERT INTO madras_canon
    (plan_id, project, vision, north_star, target_date, phases, pivots, ideas, version, tenant)
VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9,$10)
-- (plan_id, tenant), not plan_id alone: the bare target collapsed two tenants onto one row and
-- silently dropped a write -- the fourth instance of that shape in this codebase.
ON CONFLICT (plan_id, tenant) DO UPDATE SET
    project=EXCLUDED.project, vision=EXCLUDED.vision, north_star=EXCLUDED.north_star,
    target_date=EXCLUDED.target_date, phases=EXCLUDED.phases, pivots=EXCLUDED.pivots,
    ideas=EXCLUDED.ideas, version=madras_canon.version+1, updated_at=NOW()
RETURNING version
"""
_GET = "SELECT * FROM madras_canon WHERE plan_id=$1"
_GET_PROJECT = "SELECT * FROM madras_canon WHERE project=$1 ORDER BY updated_at DESC LIMIT 1"
_ALL_PROJECTS = "SELECT DISTINCT ON (project) * FROM madras_canon ORDER BY project, updated_at DESC"
_REV = """
INSERT INTO madras_canon_revisions (plan_id, author, op, summary, snapshot, tenant)
VALUES ($1,$2,$3,$4,$5::jsonb,$6)
"""
_REV_LIST = """
SELECT ts, author, op, summary FROM madras_canon_revisions
WHERE plan_id=$1 ORDER BY ts DESC LIMIT $2
"""


def _phase(d: dict[str, Any]) -> CanonPhase:
    return CanonPhase(
        id=d["id"],
        title=d.get("title", ""),
        objective=d.get("objective", ""),
        key_results=list(d.get("key_results", [])),
        status=d.get("status", "pending"),
        order=int(d.get("order", 0)),
        requirements=list(d.get("requirements", [])),
        design=d.get("design", ""),
        tasks=[
            CanonTask(
                id=t["id"],
                title=t.get("title", ""),
                status=t.get("status", "pending"),
                woop=dict(t.get("woop", {})),
                evidence=list(t.get("evidence", [])),
                blocked_by=list(t.get("blocked_by", [])),
                order=int(t.get("order", 0)),
                priority=str(t.get("priority", "")),
                urgent=bool(t.get("urgent", False)),
                impact=int(t.get("impact", 0)),
                effort=int(t.get("effort", 0)),
            )
            for t in d.get("tasks", [])
        ],
    )


def _loads(v: Any) -> list[Any]:
    return json.loads(v) if isinstance(v, str) else list(v)


def _from_row(r: asyncpg.Record) -> Canon:
    return Canon(
        plan_id=r["plan_id"],
        project=r["project"],
        vision=r["vision"],
        north_star=r["north_star"],
        version=r["version"],
        target_date=r["target_date"] if "target_date" in r else "",
        phases=[_phase(p) for p in _loads(r["phases"])],
        pivots=[CanonPivot(**p) for p in _loads(r["pivots"])],
        ideas=[CanonIdea(**i) for i in _loads(r["ideas"] if "ideas" in r else "[]")],
    )


def canon_from_payload(plan_id: str, project: str, d: dict[str, Any]) -> Canon:
    """Build a Canon from an edit payload (the inverse of canon_view). Tolerant."""
    phases: list[CanonPhase] = []
    raw_phases: list[Any] = d.get("phases") or []
    for i, p in enumerate(raw_phases):
        if not isinstance(p, dict):
            continue
        p = cast("dict[str, Any]", p)
        raw_tasks: list[Any] = p.get("tasks") or []
        tasks: list[CanonTask] = []
        for j, t in enumerate(raw_tasks):
            if not isinstance(t, dict):
                continue
            t = cast("dict[str, Any]", t)
            tasks.append(
                CanonTask(
                    id=str(t.get("id") or f"t{j}"),
                    title=str(t.get("title", "")),
                    status=str(t.get("status", "pending")),
                    woop=dict(t.get("woop", {})),
                    evidence=list(t.get("evidence", [])),
                    blocked_by=list(t.get("blocked_by", [])),
                    order=int(t.get("order", j)),
                    priority=str(t.get("priority", "")),
                    urgent=bool(t.get("urgent", False)),
                    impact=int(t.get("impact", 0)),
                    effort=int(t.get("effort", 0)),
                )
            )
        phases.append(
            CanonPhase(
                id=str(p.get("id") or f"p{i}"),
                title=str(p.get("title", "")),
                objective=str(p.get("objective", "")),
                key_results=list(p.get("key_results", [])),
                status=str(p.get("status", "pending")),
                order=int(p.get("order", i)),
                requirements=list(p.get("requirements", [])),
                design=str(p.get("design", "")),
                tasks=tasks,
            )
        )
    pivots: list[CanonPivot] = []
    raw_pivots: list[Any] = d.get("pivots") or []
    for k, pv in enumerate(raw_pivots):
        if not isinstance(pv, dict):
            continue
        pv = cast("dict[str, Any]", pv)
        pivots.append(
            CanonPivot(
                id=str(pv.get("id") or f"pivot-{k + 1}"),
                when=str(pv.get("when", "")),
                reason=str(pv.get("reason", "")),
                impact=str(pv.get("impact", "")),
                phase_id=pv.get("phase_id") or None,
                source=str(pv.get("source", "user")),
                disposition=str(pv.get("disposition", "open")),
            )
        )
    ideas: list[CanonIdea] = []
    # canon_view splits the parking lot into ideas[] + defers[]; merge both back.
    raw_ideas: list[Any] = list(d.get("ideas") or []) + list(d.get("defers") or [])
    for m, idea in enumerate(raw_ideas):
        if not isinstance(idea, dict):
            continue
        idea = cast("dict[str, Any]", idea)
        ideas.append(
            CanonIdea(
                id=str(idea.get("id") or f"idea-{m + 1}"),
                title=str(idea.get("title", "")),
                category=str(idea.get("category", "")),
                status=str(idea.get("status", "idea")),
                note=str(idea.get("note", "")),
                order=int(idea.get("order", m)),
            )
        )
    return Canon(
        plan_id=plan_id,
        project=project,
        vision=str(d.get("vision", "")),
        north_star=str(d.get("north_star", "")),
        target_date=str(d.get("target_date", "")),
        phases=phases,
        pivots=pivots,
        ideas=ideas,
    )


def _launch(target_date: str, today: str | None) -> dict[str, Any]:
    """Launch driver: the date that pulls everything + days remaining (if today given)."""
    out: dict[str, Any] = {"target_date": target_date, "days_remaining": None}
    if target_date and today:
        from datetime import date

        try:
            d0 = date.fromisoformat(today[:10])
            d1 = date.fromisoformat(target_date[:10])
            out["days_remaining"] = (d1 - d0).days
        except ValueError:
            pass
    return out


def canon_view(
    canon: Canon | None,
    revisions: list[dict[str, Any]] | None = None,
    *,
    today: str | None = None,
) -> dict[str, Any]:
    """Shape a Canon (+revisions) for the cockpit Lighthouse panel. Pure + safe on None."""
    if canon is None:
        return {
            "exists": False,
            "vision": "",
            "north_star": "",
            "version": 0,
            "coherence": 100,
            "phases": [],
            "pivots": [],
            "ideas": [],
            "defers": [],
            "revisions": [],
            "launch": {"target_date": "", "days_remaining": None},
            "roadmap": {"now": [], "next": [], "later": []},
        }
    phases: list[dict[str, Any]] = []
    for p in sorted(canon.phases, key=lambda x: x.order):
        total = len(p.tasks)
        done = sum(1 for t in p.tasks if t.status == "done")
        phases.append(
            {
                "id": p.id,
                "title": p.title,
                "objective": p.objective,
                "key_results": p.key_results,
                "status": p.status,
                "pct": round(100 * done / total) if total else (100 if p.status == "done" else 0),
                "tasks": [
                    {
                        "id": t.id,
                        "title": t.title,
                        "status": t.status,
                        "woop": t.woop,
                        "evidence": t.evidence,
                        "blocked_by": t.blocked_by,
                    }
                    for t in sorted(p.tasks, key=lambda x: x.order)
                ],
            }
        )
    open_pivots = sum(1 for pv in canon.pivots if pv.disposition == "open")
    coherence = max(40, 100 - 8 * open_pivots)
    # roadmap: active phases = now; first pending after = next; the rest = later.
    now = [p["title"] for p in phases if p["status"] == "active"]
    pending = [p["title"] for p in phases if p["status"] == "pending"]
    if not now and pending:
        now, pending = pending[:1], pending[1:]
    ideas_sorted = sorted(canon.ideas, key=lambda x: x.order)
    idea_dicts = [
        {"id": i.id, "title": i.title, "category": i.category, "status": i.status, "note": i.note}
        for i in ideas_sorted
    ]
    return {
        "exists": True,
        "vision": canon.vision,
        "north_star": canon.north_star,
        "version": canon.version,
        "coherence": coherence,
        "phases": phases,
        "target_date": canon.target_date,
        "launch": _launch(canon.target_date, today),
        "pivots": [
            {
                "id": pv.id,
                "when": pv.when,
                "reason": pv.reason,
                "impact": pv.impact,
                "phase_id": pv.phase_id,
                "disposition": pv.disposition,
            }
            for pv in canon.pivots
        ],
        # The parking lot — ideas the user can pick up later, and what's been deferred,
        # so scope decisions are visible (nothing is silently dropped).
        "ideas": [i for i in idea_dicts if i["status"] not in ("deferred", "rejected")],
        "defers": [i for i in idea_dicts if i["status"] in ("deferred", "rejected")],
        "revisions": revisions or [],
        "roadmap": {"now": now, "next": pending[:1], "later": pending[1:]},
    }


class CanonLedger:
    """One row per project canon, upserted as the plan evolves; revisions appended."""

    def __init__(self, *, postgres_url: str, tenant: str = "default") -> None:
        self._url = postgres_url
        self._tenant = tenant
        self._pool: asyncpg.Pool | None = None

    @property
    def tenant(self) -> str:
        """The namespace every read and write is scoped to -- "default" to match the rest."""
        return self._tenant

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            tenant = self._tenant

            async def _bind_tenant(conn: asyncpg.pool.PoolConnectionProxy[asyncpg.Record]) -> None:
                # `setup`, not `init`: asyncpg's RESET ALL on release wipes an init-bound value,
                # so only the first acquire would carry a tenant (D83 step 7).
                await conn.execute("SELECT set_config('madras.tenant', $1, false)", tenant)

            self._pool = await asyncpg.create_pool(
                self._url, min_size=1, max_size=4, setup=_bind_tenant
            )
        return self._pool

    async def upsert(
        self, canon: Canon, *, author: str = "user", op: str = "write", summary: str = ""
    ) -> int:
        pool = await self._get_pool()
        phases = json.dumps([asdict(p) for p in canon.phases])
        pivots = json.dumps([asdict(p) for p in canon.pivots])
        ideas = json.dumps([asdict(i) for i in canon.ideas])
        async with pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                _UPSERT,
                canon.plan_id,
                canon.project,
                canon.vision,
                canon.north_star,
                canon.target_date,
                phases,
                pivots,
                ideas,
                canon.version,
                self._tenant,
            )
            assert row is not None, "INSERT ... RETURNING always returns a row"
            version = int(row["version"])
            await conn.execute(
                _REV,
                canon.plan_id,
                author,
                op,
                summary,
                json.dumps(
                    {
                        "vision": canon.vision,
                        "north_star": canon.north_star,
                        "target_date": canon.target_date,
                        "phases": json.loads(phases),
                        "pivots": json.loads(pivots),
                        "ideas": json.loads(ideas),
                        "version": version,
                    }
                ),
                self._tenant,
            )
        return version

    async def get(self, *, plan_id: str) -> Canon | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(_GET, plan_id)
        return _from_row(r) if r else None

    async def for_project(self, *, project: str) -> Canon | None:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            r = await conn.fetchrow(_GET_PROJECT, project)
        return _from_row(r) if r else None

    async def all_projects(self) -> list[Canon]:
        """Latest canon per project — the user's portfolio for the Lighthouse hub."""
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_ALL_PROJECTS)
        return [_from_row(r) for r in rows]

    async def revisions(self, *, plan_id: str, limit: int = 50) -> list[dict[str, Any]]:
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(_REV_LIST, plan_id, limit)
        return [
            {
                "ts": r["ts"].isoformat(),
                "author": r["author"],
                "op": r["op"],
                "summary": r["summary"],
            }
            for r in rows
        ]

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
