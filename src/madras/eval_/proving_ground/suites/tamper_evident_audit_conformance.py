"""Tamper-evident audit conformance — Benchmark.md §6 axis #5 ("editing any historical record
is detectable; forensic SLA — novel, no external equivalent"), one of 5 proprietary axes
confirmed s42 to have zero suite despite being named in canon as a measured-benchmark target.

Zero-LLM, deterministic — drives the REAL `audit/chain.py::verify_chain()` against real
synthetic chains (built via the real `canonical_payload`/`compute_hash` functions, not
reimplemented), proving: a genuine untampered chain verifies clean; a tampered payload,
a tampered hash, and a deleted record are each detected at the correct index.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["tamper_evident_audit"]


def _build_chain(n: int) -> list[dict[str, Any]]:
    from madras.audit.chain import GENESIS, canonical_payload, compute_hash

    records: list[dict[str, Any]] = []
    prev = GENESIS
    for i in range(n):
        fields: dict[str, Any] = {
            "agent_name": "shadow",
            "session_id": "s1",
            "action": f"action_{i}",
            "signals": {"step": i},
            "tool_calls": [],
            "extras": {},
        }
        payload = canonical_payload(
            agent_name=fields["agent_name"],
            session_id=fields["session_id"],
            action=fields["action"],
            signals=fields["signals"],
            tool_calls=fields["tool_calls"],
            extras=fields["extras"],
        )
        record_hash = compute_hash(prev, payload)
        records.append({**fields, "prev_hash": prev, "record_hash": record_hash})
        prev = record_hash
    return records


def _case_genuine_chain_verifies_clean() -> tuple[bool, str]:
    from madras.audit.chain import verify_chain

    result = verify_chain(_build_chain(5))
    ok = result.ok is True and result.length == 5 and result.broken_at is None
    return ok, f"ok={result.ok} length={result.length}"


def _case_tampered_payload_detected_at_correct_index() -> tuple[bool, str]:
    from madras.audit.chain import verify_chain

    records = _build_chain(5)
    records[2]["signals"] = {"step": 999}  # mutate a stored field post-hoc, hash now stale
    result = verify_chain(records)
    ok = result.ok is False and result.broken_at == 2
    return ok, f"ok={result.ok} broken_at={result.broken_at}"


def _case_tampered_hash_detected() -> tuple[bool, str]:
    from madras.audit.chain import verify_chain

    records = _build_chain(5)
    records[3]["record_hash"] = "0" * 64  # forge the hash directly
    result = verify_chain(records)
    ok = result.ok is False and result.broken_at == 3
    return ok, f"ok={result.ok} broken_at={result.broken_at}"


def _case_deleted_record_breaks_the_chain() -> tuple[bool, str]:
    from madras.audit.chain import verify_chain

    records = _build_chain(5)
    del records[2]  # remove a record entirely — the next record's prev_hash no longer matches
    result = verify_chain(records)
    ok = result.ok is False and result.broken_at == 2
    return ok, f"ok={result.ok} broken_at={result.broken_at}"


def _case_empty_chain_is_vacuously_valid() -> tuple[bool, str]:
    from madras.audit.chain import verify_chain

    result = verify_chain([])
    ok = result.ok is True and result.length == 0
    return ok, f"ok={result.ok} length={result.length}"


_EXECUTORS: dict[str, Any] = {
    "genuine_chain_verifies_clean": _case_genuine_chain_verifies_clean,
    "tampered_payload_detected_at_correct_index": _case_tampered_payload_detected_at_correct_index,
    "tampered_hash_detected": _case_tampered_hash_detected,
    "deleted_record_breaks_the_chain": _case_deleted_record_breaks_the_chain,
    "empty_chain_is_vacuously_valid": _case_empty_chain_is_vacuously_valid,
}


def _run_case(case_id: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        passed, detail = _EXECUTORS[case_id]()
    except Exception as exc:
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": case_id,
        "suite_id": "tamper_evident_audit_conformance",
        "benchmark_family": "tamper_evident_audit_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "chain_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class TamperEvidentAuditConformanceSuite(Suite):
    id: str = "tamper_evident_audit_conformance"
    name: str = "Tamper-evident audit conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct calls into the REAL "
        "audit/chain.py::verify_chain()/canonical_payload()/compute_hash() over synthetic "
        "chains. Fills Benchmark.md §6 axis #5 (tamper-evident audit), confirmed s42 to have "
        "zero suite despite being named in canon as a measured-benchmark target."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"tamper_evident_audit_conformance-{case_id}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=list(_FEATURES),
                tools=[],
                prompt=f"[conformance] {case_id}",
                setup={},
                checks=[],
            )
            for case_id in _EXECUTORS
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency
        return [_run_case(case_id) for case_id in _EXECUTORS]
