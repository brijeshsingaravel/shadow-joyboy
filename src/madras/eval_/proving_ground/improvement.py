"""Improvement engine — turn a run's failures into a ranked, actionable backlog.

A scorecard says where you are; this says what to FIX next. cluster_failures groups
failed scenarios by (feature, failure_kind); backlog_from_clusters maps each kind to a
known hardening lever and ranks by count x cost so the highest-leverage fix surfaces
first. Deterministic — no LLM. The backlog auto-emits into Lighthouse (G4).
"""

from __future__ import annotations

from typing import Any, cast

# failure_kind -> the known lever that addresses it (the hardening playbook).
FIX_HINTS: dict[str, str] = {
    "tool_called": "schema-guided tool-call repair (T3-3) + sharper tool descriptions",
    "tool_args_subset": "schema-guided arg decoding + required-field coercion (T3-3)",
    "answer_contains": "memory recall / grounding — verify the fact is retrieved before answering",
    "answer_regex": "output-format adherence — constrained decoding for the answer shape",
    "no_unsafe_tool": "tighten the rank gate / guardrail for the escalated tool",
    "refused": "calibrate refusal — over- or under-refusing the safety boundary",
    "EmptyCompletion": "empty-completion retry + temperature lowering (FREE-tier reliability)",
    "Timeout": "raise per-call timeout or split the task; check provider rate caps",
    "error": "classify the error + bounded retry / model fallback",
}
_DEFAULT_FIX = "inspect the failure cluster and add a targeted hardening pass"


def _kind(row: dict[str, Any]) -> str:
    if row.get("error"):
        err = str(row["error"])
        for k in ("EmptyCompletion", "Timeout"):
            if k.lower() in err.lower():
                return k
        return "error"
    return row.get("first_failed_check") or "unknown"


def cluster_failures(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group FAILED rows by (feature, failure_kind), ranked by count x mean cost."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.get("passed"):
            continue
        feature = r.get("feature") or r.get("benchmark_family") or "unknown"
        kind = _kind(r)
        key = (feature, kind)
        g = groups.setdefault(
            key, {"feature": feature, "failure_kind": kind, "count": 0, "cost": 0.0, "examples": []}
        )
        g["count"] += 1
        g["cost"] += float(r.get("cost_usd") or 0.0)
        if len(g["examples"]) < 5:
            g["examples"].append(r.get("scenario_id"))
    out = list(groups.values())
    out.sort(key=lambda g: (g["count"], g["cost"]), reverse=True)
    return out


def backlog_from_clusters(clusters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Map each cluster to a known fix lever + a priority; ranked highest-leverage first."""
    backlog: list[dict[str, Any]] = []
    for c in clusters:
        backlog.append(
            {
                "id": f"pgfix-{c['feature']}-{c['failure_kind']}",
                "feature": c["feature"],
                "failure_kind": c["failure_kind"],
                "count": c["count"],
                "examples": c["examples"],
                "suggested_fix": FIX_HINTS.get(c["failure_kind"], _DEFAULT_FIX),
                "priority": c["count"],
            }
        )
    backlog.sort(key=lambda b: b["priority"], reverse=True)
    return backlog


def emit_to_lighthouse(
    backlog: list[dict[str, Any]], status: dict[str, Any], *, top_n: int = 5
) -> dict[str, Any]:
    """Merge the top-N backlog items into STATUS.json.open_items[] (idempotent by id)."""
    items: list[Any] = status.setdefault("open_items", [])
    existing = {cast("dict[str, Any]", i).get("id") for i in items if isinstance(i, dict)}
    for b in backlog[:top_n]:
        if b["id"] in existing:
            continue
        items.append(
            {
                "id": b["id"],
                "source": "proving-ground",
                "title": f"[{b['feature']}/{b['failure_kind']}] x{b['count']} "
                f"-> {b['suggested_fix']}",
                "status": "open",
            }
        )
    return status
