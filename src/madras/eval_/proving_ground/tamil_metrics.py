"""T5.3 -- the DSL decision-gate metrics RFC-0001 needs to graduate `status: draft` -> accepted.

Grammar-conformance rate and round-trip fidelity, computed over a corpus of `.tamil` source
programs and wired into the existing eval-lab pipeline (`log_experiment`) as ordinary metrics --
no new tracking infrastructure, the same MLflow run every other eval-lab metric lands on.
"""

from __future__ import annotations

from tamil_lang import Call, Goal, Govern, parse


def grammar_conformance_rate(programs: list[str]) -> float:
    """Fraction of `programs` that parse without raising -- the simplest possible measure of
    "does real `.tamil` source stay inside the kernel + genome grammar" (RFC-0002 §4.3)."""
    if not programs:
        return 1.0
    ok = 0
    for src in programs:
        try:
            parse(src)
        except Exception:  # any parse failure counts against conformance
            continue
        ok += 1
    return ok / len(programs)


def _reemit(goal: Goal) -> str:
    """Re-render a single-goal, single-statement `govern` + `call` program canonically -- the
    same shape `test_round_trip_goal` (Hypothesis) already proves round-trips for the front-end;
    this reuses that exact logic as a reusable metric rather than a one-off test assertion."""
    govern, call = goal.body[0], goal.body[1]
    assert isinstance(govern, Govern)
    assert isinstance(call, Call)
    return f'goal "{goal.intent}" {{ govern {govern.check}  call {call.name}() }}'


def round_trip_fidelity(programs: list[str]) -> float:
    """Fraction of `programs` where `parse -> re-render -> parse` yields an identical AST --
    `AST -> render -> weights -> AST -> parse` (RFC-0002 §6.4/§9), the front-end half of that
    chain proven as a metric, not just a unit test. Programs outside the
    `goal "..." { govern ...  call f() }` shape (what `_reemit` can re-render) are skipped, not
    counted as failures -- this metric is scoped to the shape RFC-0001's own decision gate uses."""
    reemittable: list[Goal] = []
    for src in programs:
        try:
            (goal,) = parse(src)
            if (
                len(goal.body) == 2
                and isinstance(goal.body[0], Govern)
                and isinstance(goal.body[1], Call)
            ):
                reemittable.append(goal)
        except Exception:
            continue
    if not reemittable:
        return 1.0
    stable = 0
    for goal in reemittable:
        try:
            (goal2,) = parse(_reemit(goal))
            if goal2 == goal:
                stable += 1
        except Exception:
            continue
    return stable / len(reemittable)


__all__ = ["grammar_conformance_rate", "round_trip_fidelity"]
