"""W0·3 — the outlier-metric spine: the lens every later workstream is graded by.

Two pieces, both reusing what already exists:

1. ``run_compounding_track`` — runs the 30-session compounding track (``suites/compounding.py``)
   IN ORDER with a SHARED memory namespace, aggregates per-session ``pass_rate`` + ``cost_of_pass``,
   and feeds ``compounding_efficiency`` (the signature: quality-lift + cost-decay). The curve is
   FLAT until the memory layers are wired (W1) — that flat baseline is honest; moving it is W1's
   success criterion, and the published number is F1.
2. ``outlier_verdict`` — assembles the FULLER verdict from the already-computed leaderboard row
   (madras_index · scaffold_lift · cost_of_pass · tokens_per_task · speed_tok_s — `leaderboard.py`),
   ``pass_k`` (already on ``pg_model_runs``), and the compounding signature, then grades it.

Pure functions: no I/O, no LLM. The live run (real governed loop + shared memory) injects
``run_session``; persistence/surfacing lives in ``store_v2`` + ``leaderboard``.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from typing import Any

from madras.eval_.proving_ground.suite import Case
from madras.eval_.proving_ground.suites.compounding import compounding_efficiency


def run_compounding_track(
    cases: list[Case],
    run_session: Callable[..., dict[str, float]],
    *,
    namespace: str,
) -> dict[str, Any]:
    """Run the compounding track in session order; return the compounding signature.

    ``run_session(case, namespace=...)`` runs one case under the shared memory namespace and
    returns ``{"pass_rate": float, "cost_of_pass": float}`` (the live governed loop at W1; a
    deterministic fn in tests). Sessions are aggregated by ``session_index`` and fed to
    ``compounding_efficiency``. Returns ``{quality_lift, cost_decay, compounding, n_sessions,
    sessions}``.
    """
    by_session: dict[int, list[Case]] = defaultdict(list)
    for c in cases:
        si = int(c.setup.get("session_index") or 0)
        by_session[si].append(c)

    sessions: list[dict[str, Any]] = []
    for si in sorted(by_session):
        scases = by_session[si]
        kind = scases[0].setup.get("kind", "recall")
        results = [run_session(c, namespace=namespace) for c in scases]
        n = len(results) or 1
        pass_rate = sum(float(r.get("pass_rate", 0.0)) for r in results) / n
        cost = sum(float(r.get("cost_of_pass", 0.0)) for r in results) / n
        sessions.append(
            {
                "session_index": si,
                "kind": kind,
                "pass_rate": round(pass_rate, 4),
                "cost_of_pass": round(cost, 6),
            }
        )

    eff = compounding_efficiency(sessions)
    return {**eff, "n_sessions": len(sessions), "sessions": sessions}


# s33 moat class -> its conformance suite id -> its explicit outlier signal name (C6, DC2: these
# are first-class in the verdict, not diluted into the Index average). A signal is None until its
# suite has actually run (not-yet-measured doesn't block, matching the compounding convention);
# once measured, True only at the suite's target (1.0 — a correctness/security invariant, not a
# climb-from bar), so ANY regression below it flips the signal False.
MOAT_SUITES: dict[str, str] = {
    "identity_boundary_conformance": "governance_holds",
    "routing_resilience_conformance": "routing_degrades_gracefully",
    "durable_state_conformance": "survives_restart",
    "compile_conformance": "compile_conformant",
    "memory_sovereignty_conformance": "memory_portable",
}


def _moat_signals(conformance: dict[str, float] | None) -> dict[str, bool | None]:
    conf = conformance or {}
    signals: dict[str, bool | None] = {}
    for suite_id, signal_name in MOAT_SUITES.items():
        rate = conf.get(suite_id)
        signals[signal_name] = None if rate is None else rate >= 1.0
    return signals


def moat_conformance_rates() -> dict[str, float]:
    """Run all 5 s33 conformance suites live (zero-LLM, `kind="external"`, ~instant) and return
    ``{suite_id: pass_rate}`` — the ``conformance`` dict `outlier_verdict` + the Founder-cockpit
    moat panel both consume. One source, never duplicated."""
    from madras.eval_.proving_ground.suites import SUITES  # local import avoids a load-time cycle

    rates: dict[str, float] = {}
    for suite_id in MOAT_SUITES:
        suite = SUITES[suite_id]
        rows = suite.run("n/a", 1, 1)
        n = len(rows) or 1
        rates[suite_id] = sum(1 for r in rows if r.get("verdict") == "pass") / n
    return rates


def moat_status() -> dict[str, Any]:
    """The moat portion of the outlier verdict on its own (no board_row/pass_k/compounding
    needed) — what the Founder-cockpit panel and any standalone health-check consume. Runs the
    5 suites live via `moat_conformance_rates`."""
    rates = moat_conformance_rates()
    signals = _moat_signals(rates)
    return {
        "rates": rates,
        "signals": signals,
        "moat_holds": all(v is not False for v in signals.values()),
    }


def outlier_verdict(
    *,
    board_row: dict[str, Any],
    pass_k: float | None,
    compounding: dict[str, Any] | None,
    conformance: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Assemble + grade the fuller outlier verdict — the standing "are we an outlier?" lens.

    Grade: an agent is an *outlier* when it beats the bare model (``scaffold_lift > 0``), its
    memory is not regressing (``compounding >= 0`` or not-yet-measured), AND the s33 architectural
    moat holds (every MEASURED conformance suite in ``conformance`` — suite_id -> pass_rate — is at
    its 1.0 invariant; unmeasured suites don't block). Compounding is flat until W1, so a positive
    scaffold-lift carries the verdict pre-W1; once memory is live, compounding turns it into the
    step-change no competitor can produce — and the moat signals make the governance/durability/
    routing/compile/memory guarantees an explicit, checkable part of "outlier", not an average.
    """
    comp = compounding or {}
    compounding_val = comp.get("compounding")
    scaffold = board_row.get("scaffold_lift")
    beats_bare = scaffold is not None and scaffold > 0
    moat = _moat_signals(conformance)
    moat_holds = all(v is not False for v in moat.values())  # None (unmeasured) doesn't block
    is_outlier = bool(
        beats_bare and (compounding_val is None or compounding_val >= 0) and moat_holds
    )

    return {
        "madras_index": board_row.get("madras_index"),
        "scaffold_lift": scaffold,
        "cost_of_pass": board_row.get("cost_of_pass"),
        "tokens_per_task": board_row.get("tokens_per_task"),
        "speed_tok_s": board_row.get("speed_tok_s"),
        "pass_k": pass_k,
        "compounding": compounding_val,
        "quality_lift": comp.get("quality_lift"),
        "cost_decay": comp.get("cost_decay"),
        "is_outlier": is_outlier,
        "moat_holds": moat_holds,
        "signals": {
            "beats_bare_model": beats_bare,
            "memory_compounds": (None if compounding_val is None else compounding_val > 0),
            **moat,
        },
    }
