"""Sweep profiles — quick (curated top-N) vs deep (everything).

The Proving Ground runs in two depths:

- **quick** (``Run Sweep``): a curated set of the ``QUICK_LIMIT`` best tasks
  drawn ONLY from the light, locally-runnable suites (no external/infra-gated
  harness). Foreground, fast, broad — a daily smoke that still touches every
  feature + benchmark family.
- **deep** (``Run Deep Sweep``): every case across every registered suite,
  including the heavy external ones (τ²-bench, SWE-bench, AgentBench, …). Runs in
  the background and takes its time.

"Best tasks" is not a hand-picked magic list — it is computed deterministically
by ``select_top_cases``: a coverage-greedy ranker that first guarantees breadth
(every distinct feature + benchmark family represented) and then fills the
remaining slots with the highest-quality, best-specified cases. Same vendored
slices in → same ``QUICK_LIMIT`` out, so the quick set is reproducible and
explainable.

``QUICK_LIMIT`` must stay >= the TRUE distinct ``benchmark_family`` count across
every light suite's cases (NOT a naive one-per-suite-name count — a single
suite, e.g. ``madras_features``, can carry several internal family tags) so
full breadth coverage stays achievable. Bumped 25->42 in C7 (framework-10x
Part C) once the s33 harvest grew the true family count to 38 (+ margin for
quality-fill); re-bump deliberately (never silently) if it grows again.
"""

from __future__ import annotations

from madras.eval_.proving_ground.suite import Case
from madras.eval_.proving_ground.suites import SUITES

QUICK_LIMIT = 42
SMOKE_LIMIT = (
    10  # T2.11: the fastest sanity tier — a tiny curated subset, not a real regression check
)

# T2.11 tier aliases: smoke/regression select suites the way "quick" always has (curated,
# external-kind suites stripped — quick/regression never trigger a heavy self-running harness);
# nightly/release-certification select suites the way "deep" always has (everything, external
# included). release-certification's held-out + gaming-scan gate is evaluated by the caller
# after run_sweep completes (case_limit_for_profile below only controls case COUNT, not the
# extra certification checks) — see run_evaluation_lab.py.
_QUICK_LIKE_PROFILES = frozenset({"quick", "regression", "smoke"})
_DEEP_LIKE_PROFILES = frozenset({"deep", "nightly", "release-certification"})


def quick_suites() -> list[str]:
    """Light, locally-runnable suites for the quick sweep (external excluded)."""
    return [name for name, suite in SUITES.items() if suite.kind != "external"]


def deep_suites() -> list[str]:
    """Every registered suite — the deep sweep runs them all (external included)."""
    return list(SUITES)


def _quality(case: Case) -> float:
    """Specification quality — better-specified cases give cleaner signal.

    A rubric and deterministic checks both anchor judging; declared tools/features
    make a case diagnostic for coverage. Used only as the tie-breaker AFTER
    coverage breadth is satisfied.
    """
    score = 0.0
    if case.rubric.strip():
        score += 1.0
    if case.checks:
        score += 1.0
    if case.tools:
        score += 0.5
    if case.features:
        score += 0.5
    return score


def select_top_cases(cases: list[Case], limit: int = QUICK_LIMIT) -> list[Case]:
    """Pick the ``limit`` most valuable cases, breadth first then quality.

    Greedy: at each step take the case that adds a not-yet-seen benchmark family
    FIRST (a strict, non-tradeable priority — see the C7 fix note below), then the
    most NEW feature coverage, then specification quality, then a stable
    ``(suite_id, id)`` order so the result is fully deterministic. Once coverage
    is saturated the remaining slots fill with the highest-quality cases. Returns
    at most ``limit`` cases (all of them if fewer).

    C7 (framework-10x Part C) fix: the ranking used to be ADDITIVE
    (``new_features + new_family``), which let a same-family case with several
    new features TIE a genuinely-new-family case with fewer — and ties broke by
    alphabetical ``(suite_id, id)``, silently starving family coverage once
    suites started contributing thousands of fine-grained-feature cases (the
    s33 harvest). The rank is now a proper LEXICOGRAPHIC tuple
    ``(new_family, new_features, quality)`` — ``new_family`` (0 or 1) is compared
    FIRST and strictly dominates, so an uncovered family always outranks any
    same-family repeat regardless of feature count.
    """
    if limit <= 0:
        return []
    ordered = sorted(cases, key=lambda c: (c.suite_id, c.id))
    remaining = list(ordered)
    selected: list[Case] = []
    covered_features: set[str] = set()
    covered_families: set[str] = set()

    while remaining and len(selected) < limit:

        def rank(case: Case) -> tuple[int, int, float]:
            new_family = 0 if case.benchmark_family in covered_families else 1
            new_features = len(set(case.features) - covered_features)
            return (new_family, new_features, _quality(case))

        # `remaining` is in deterministic order, so max() picks the first best on
        # ties → reproducible selection.
        best = max(remaining, key=rank)
        selected.append(best)
        remaining.remove(best)
        covered_features.update(best.features)
        covered_families.add(best.benchmark_family)

    return selected


def resolve_profile_suites(profile: str, requested: list[str] | None) -> list[str]:
    """Suite list for a profile: explicit ``requested`` wins, else the default.

    ``quick``/``regression``/``smoke`` default to the light suites; ``deep``/``nightly``/
    ``release-certification`` to all of them (T2.11 tier aliases — see
    ``_QUICK_LIKE_PROFILES``/``_DEEP_LIKE_PROFILES`` above). An explicit request is still
    honored (so a caller can deep-run a single suite), but the quick-like tiers strip
    external suites — they never trigger a heavy self-running harness.
    """
    is_quick_like = profile in _QUICK_LIKE_PROFILES
    if requested:
        if is_quick_like:
            return [s for s in requested if SUITES.get(s) and SUITES[s].kind != "external"]
        return list(requested)
    return quick_suites() if is_quick_like else deep_suites()


def case_limit_for_profile(profile: str) -> int | None:
    """The curated-case cap for a profile (T2.11), or None when uncapped.

    ``smoke`` caps at ``SMOKE_LIMIT`` (a tiny sanity subset); ``quick``/``regression`` cap at
    ``QUICK_LIMIT`` (today's curated-breadth set); ``deep``/``nightly``/``release-certification``
    are uncapped — the full suite battery.
    """
    if profile == "smoke":
        return SMOKE_LIMIT
    if profile in ("quick", "regression"):
        return QUICK_LIMIT
    return None
