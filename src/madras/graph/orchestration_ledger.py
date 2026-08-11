"""Dual-ledger orchestration — Magentic-One pattern, governed for Shadow.

The supervisor's delegation is disciplined by two ledgers (Microsoft Magentic-One):

* **Task Ledger** (outer loop): the objective + what's known + the planned assignments.
  Each assignment carries Anthropic's four-part subagent contract — objective, output
  format, tool guidance, boundaries — because short prompts ("research X") are the #1
  cause of duplicated/under-specified subagent work.
* **Progress Ledger** (inner loop): after each delegation round, what's done vs.
  outstanding, and whether the run has STALLED (no new progress). Two stalls → replan.
  This directly attacks the MAST failure buckets (step-repetition, non-termination).

All functions here are PURE + deterministic (no LLM, no I/O) so they're fully testable;
the live LLM work (running children, the synthesizer) lives in the delegate tool.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Effort-scaling guidance (Anthropic multi-agent research system): scale subagents to
# query complexity to avoid the "50 subagents for a simple query" failure.
EFFORT_GUIDANCE = (
    "Scale effort to complexity: a simple fact needs NO delegation (answer inline); a "
    "direct comparison needs 2-4 focused subagents; only genuinely broad, parallelizable "
    "work justifies more. Never spawn a subagent for something you can do in one step."
)


@dataclass
class SubAssignment:
    """One worker assignment — the four-part subagent contract."""

    role: str
    task: str  # the objective for this worker
    output_format: str = ""  # what shape the worker should return
    boundaries: str = ""  # what this worker must NOT do (anti-duplication)


def subtask_contract(objective: str, a: SubAssignment) -> str:
    """Render an assignment as a full four-part contract prompt for the worker.

    Weak open models drift without explicit structure, so every field is spelled out
    even when the supervisor left it blank (sensible defaults).
    """
    lines = [f"YOUR TASK: {a.task.strip()}"]
    if objective.strip():
        lines.append(f"THIS SERVES THE LARGER GOAL: {objective.strip()}")
    lines.append(
        "OUTPUT FORMAT: "
        + (a.output_format.strip() or "A concise, factual summary of what you found.")
    )
    lines.append(
        "BOUNDARIES: "
        + (
            a.boundaries.strip()
            or "Stay strictly within this task. Do NOT do the other parts of the goal — "
            "other workers own those. Report only your own findings."
        )
    )
    return "\n".join(lines)


@dataclass
class ProgressLedger:
    """Inner-loop self-reflection: progress vs. outstanding work + stall tracking."""

    objective: str = ""
    completed: list[str] = field(default_factory=list[str])
    outstanding: list[str] = field(default_factory=list[str])
    stalled_rounds: int = 0

    @property
    def is_complete(self) -> bool:
        return not self.outstanding

    def record_round(self, newly_completed: list[str]) -> None:
        """Fold a round's results in. No new completion → a stall (toward replan)."""
        gained = [c for c in newly_completed if c not in self.completed]
        if gained:
            self.completed.extend(gained)
            self.outstanding = [o for o in self.outstanding if o not in gained]
            self.stalled_rounds = 0
        else:
            self.stalled_rounds += 1


def should_replan(ledger: ProgressLedger, *, max_stall: int = 2) -> bool:
    """Replan when the run has stalled for `max_stall` rounds with work outstanding
    (Magentic-One: re-derive the plan after two stalled iterations)."""
    return ledger.stalled_rounds >= max_stall and not ledger.is_complete


def dedupe_findings(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop workers that returned the same summary (near-identical fan-out output is a
    classic duplication failure). First occurrence wins; order preserved."""
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in results:
        key = " ".join((r.get("summary") or "").split()).lower()[:400]
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        out.append(r)
    return out


def synthesis_prompt(objective: str, results: list[dict[str, Any]]) -> str:
    """Build the synthesizer child's prompt: reconcile worker outputs into ONE coherent,
    de-duplicated, attributed answer — not a concatenation of summaries."""
    blocks: list[str] = []
    for i, r in enumerate(results, 1):
        role = r.get("role", "worker")
        summary = (r.get("summary") or "").strip()
        blocks.append(f'<worker id="{i}" role="{role}">\n{summary}\n</worker>')
    joined = "\n\n".join(blocks)
    return (
        "You are a synthesis agent. Below are findings from several worker subagents that "
        "each handled part of a larger goal. Reconcile them into ONE coherent answer that "
        "directly serves the goal. Merge overlapping points, drop duplicates, resolve any "
        "contradictions (note them if unresolved), and keep attribution to which worker "
        "found what where it matters. Do not invent facts beyond the workers' findings.\n\n"
        f"GOAL: {objective.strip() or '(synthesize the findings)'}\n\n"
        f"<retrieved>\n{joined}\n</retrieved>"
    )
