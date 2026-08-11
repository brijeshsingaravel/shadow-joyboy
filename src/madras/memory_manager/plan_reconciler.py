"""Plan reconciler — reconciles the structured plan ledger (intent) against the
raw session log (evidence) so plan-item completion is earned, never self-asserted.

This is the anti-drift step: an open plan item is only marked ``done`` when raw
session evidence supports it, and items with no build evidence across several
recent sessions are flagged ``drift`` (never deleted). It runs nightly alongside
the consolidate/extract steps.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from madras.mindpalace.ledger import SessionRecord
from madras.mindpalace.plan_ledger import PlanLedger

_STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "be",
        "are",
        "was",
        "were",
        "this",
        "that",
        "it",
        "as",
        "by",
        "at",
        "from",
        "into",
        "via",
        "using",
        "use",
        "add",
        "make",
    }
)


@dataclass
class ItemVerdict:
    plan_id: str
    item_id: str
    text: str
    old_status: str
    new_status: str  # done | drift | unchanged
    evidence: list[str] = field(default_factory=list[str])
    note: str = ""


@dataclass
class ReconcileReport:
    items_confirmed_done: int
    items_flagged_drift: int
    verdicts: list[ItemVerdict]


def _keywords(text: str) -> set[str]:
    """Lowercase, split on non-alphanumeric, drop stopwords + tokens shorter than 3."""
    out: set[str] = set()
    token = ""
    for ch in text.lower():
        if ch.isalnum():
            token += ch
        else:
            if len(token) >= 3 and token not in _STOPWORDS:
                out.add(token)
            token = ""
    if len(token) >= 3 and token not in _STOPWORDS:
        out.add(token)
    return out


def match_evidence(item_text: str, session: SessionRecord) -> list[str]:
    """Deterministic heuristic — return evidence strings from ``session`` that support
    ``item_text``.

    An evidence string supports the item when the matched item-keywords reach an
    overlap ratio >= 0.5 AND at least 2 keywords match (or all keywords match when
    the item has fewer than 2 keywords).
    """
    item_kw = _keywords(item_text)
    if not item_kw:
        return []
    total = len(item_kw)
    evidence_strings = session.decisions + session.files_touched + session.tags + [session.summary]
    supporting: list[str] = []
    for ev in evidence_strings:
        if not ev:
            continue
        ev_kw = _keywords(ev)
        matched = sum(1 for kw in item_kw if kw in ev_kw)
        if matched == 0:
            continue
        ratio = matched / total
        enough = (matched >= 2) if total >= 2 else (matched == total)
        if ratio >= 0.5 and enough:
            supporting.append(f"{session.session_id}:{ev[:60]}")
    return supporting


async def reconcile_plans(
    *,
    plan_ledger: PlanLedger,
    sessions: list[SessionRecord],
    drift_after_sessions: int = 2,
    agent_name: str = "shadow",
    project: str = "default",
) -> ReconcileReport:
    """Reconcile open plans against raw session evidence. Conservative: never marks an
    item ``done`` without at least one evidence string; drift is a flag, not a deletion.
    """
    plans = await plan_ledger.list_open(project=project, agent_name=agent_name)
    verdicts: list[ItemVerdict] = []
    done_count = 0
    drift_count = 0

    for plan in plans:
        changed = False
        for item in plan.items:
            if item.status not in {"pending", "in_progress"}:
                continue
            old_status = item.status
            evidence: list[str] = []
            for session in sessions:
                evidence.extend(match_evidence(item.text, session))

            if evidence:
                new_status = "done"
                note = "confirmed by raw session evidence"
                merged = list(dict.fromkeys(item.evidence + evidence))
                item.evidence = merged
                item.status = "done"
                changed = True
                done_count += 1
            elif not evidence and len(sessions) >= drift_after_sessions and old_status == "pending":
                new_status = "drift"
                note = f"no build evidence across {len(sessions)} recent sessions"
                item.status = "drift"
                changed = True
                drift_count += 1
            else:
                new_status = "unchanged"
                note = ""

            verdicts.append(
                ItemVerdict(
                    plan_id=plan.plan_id,
                    item_id=item.id,
                    text=item.text,
                    old_status=old_status,
                    new_status=new_status,
                    evidence=evidence,
                    note=note,
                )
            )

        if changed:
            if all(i.status == "done" for i in plan.items):
                plan.status = "complete"
            await plan_ledger.upsert(plan)

    return ReconcileReport(
        items_confirmed_done=done_count,
        items_flagged_drift=drift_count,
        verdicts=verdicts,
    )
