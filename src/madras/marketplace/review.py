"""Structured review — the ClawSweeper-style review contract + marketplace-submission gate.

A reviewer (LLM or rule-based) produces `ReviewFinding`s + `ReviewMetrics`; this layer turns
them into a deterministic structured review (`reviewMetrics` · `risks` · `mergeRiskLabel` ·
`bestSolution`) and a submission gate. The merge-risk label and the gate are PURE — the findings
are the (injected) input, the verdict is deterministic — so the same submission always gates the
same way. Composes the [[Marketplace Manifest]] (B20), [[Dependency Vuln Scan]] (B30), and
[[Skill Integrity]] (B31) checks into one allow/block decision.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SEVERITIES = ("critical", "high", "medium", "low", "info")
_SEV_RANK = {s: i for i, s in enumerate(SEVERITIES)}  # 0 = worst
MERGE_RISK = ("block", "high", "medium", "low")


@dataclass
class ReviewFinding:
    category: str  # correctness | security | performance | style | tests
    severity: str  # critical | high | medium | low | info
    detail: str = ""
    file: str = ""
    line: int = 0  # row open-code-review — 0 = not line-specific


@dataclass
class ReviewMetrics:
    correctness: float = 1.0
    security: float = 1.0
    performance: float = 1.0
    maintainability: float = 1.0
    test_coverage: float = 1.0

    def values(self) -> list[float]:
        return [
            self.correctness,
            self.security,
            self.performance,
            self.maintainability,
            self.test_coverage,
        ]

    def min(self) -> float:
        return min(self.values())


@dataclass
class StructuredReview:
    metrics: ReviewMetrics
    findings: list[ReviewFinding] = field(default_factory=list[ReviewFinding])
    risks: list[ReviewFinding] = field(default_factory=list[ReviewFinding])
    merge_risk: str = "low"
    best_solution: str = ""
    summary: str = ""


def worst_severity(findings: list[ReviewFinding]) -> str | None:
    """The most severe finding's severity, or None if there are no findings."""
    sevs = [f.severity for f in findings if f.severity in _SEV_RANK]
    return min(sevs, key=lambda s: _SEV_RANK[s]) if sevs else None


def merge_risk_label(metrics: ReviewMetrics, findings: list[ReviewFinding]) -> str:
    """Deterministic merge-risk label from metrics + findings (worst signal wins)."""
    worst = worst_severity(findings)
    if worst == "critical" or metrics.security < 0.5 or metrics.correctness < 0.5:
        return "block"
    if worst == "high" or metrics.min() < 0.6:
        return "high"
    if worst == "medium" or metrics.min() < 0.8:
        return "medium"
    return "low"


def build_review(
    findings: list[ReviewFinding],
    metrics: ReviewMetrics,
    *,
    best_solution: str = "",
    summary: str = "",
) -> StructuredReview:
    """Assemble the structured review: risks = the high+ findings; merge-risk = deterministic."""
    risks = sorted(
        (f for f in findings if _SEV_RANK.get(f.severity, 99) <= _SEV_RANK["high"]),
        key=lambda f: _SEV_RANK.get(f.severity, 99),
    )
    return StructuredReview(
        metrics=metrics,
        findings=list(findings),
        risks=risks,
        merge_risk=merge_risk_label(metrics, findings),
        best_solution=best_solution,
        summary=summary,
    )


@dataclass
class GateDecision:
    allow: bool
    merge_risk: str
    reasons: list[str] = field(default_factory=list[str])


def submission_gate(
    review: StructuredReview,
    *,
    manifest_ok: bool = True,
    integrity_ok: bool = True,
) -> GateDecision:
    """The marketplace-submission gate: block on review merge-risk 'block', an invalid manifest
    ([[Marketplace Manifest]]), or failed skill integrity ([[Skill Integrity]])."""
    reasons: list[str] = []
    if review.merge_risk == "block":
        reasons.append(f"review merge-risk is 'block' ({len(review.risks)} high+ risks)")
    if not manifest_ok:
        reasons.append("manifest failed validation")
    if not integrity_ok:
        reasons.append("skill integrity check failed")
    return GateDecision(allow=not reasons, merge_risk=review.merge_risk, reasons=reasons)
