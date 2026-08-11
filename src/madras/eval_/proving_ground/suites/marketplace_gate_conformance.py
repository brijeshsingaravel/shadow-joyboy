"""Marketplace gate conformance — the deterministic conformance suite for a subsystem that
wasn't tracked in suite_subsystem.py at all until s42's deeper cross-system pass.

Product/Marketplace.md's core security/quality claim: **"No listing gets ambient trust"** — an
agent must clear `compiler/optimize.py::compile_to_residency`'s real verify→GEPA loop before
`compiler/marketplace.py::sell_agent` will even attempt to shell out to the real Mercur/Medusa
listing script. This is the exact gate that justifies the 85/15 split and the "Madras-Verified"
premium — if it can be silently bypassed, the whole marketplace trust claim is hollow.

Zero-LLM, zero-subprocess: mocks the two async dependencies (`compile_to_residency`,
`_run_listing_script`) and drives the REAL `sell_agent` gating logic against them.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Literal, cast
from unittest.mock import AsyncMock, patch

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite

_FEATURES = ["marketplace_gate"]


def _fake_residency(*, verified: bool, agent_name: str = "test_agent") -> Any:
    from madras.compiler.optimize import ResidencyResult

    record = None
    if verified:
        record = AsyncMock()
        record.config.name = agent_name
        record.config.capabilities = ["web_search"]
    return ResidencyResult(verified=verified, rounds=1, lift=0.1, record=record)


def _case_unverified_never_lists() -> tuple[bool, str]:
    from madras.compiler.marketplace import sell_agent

    with (
        patch(
            "madras.compiler.marketplace.compile_to_residency",
            new=AsyncMock(return_value=_fake_residency(verified=False)),
        ),
        patch("madras.compiler.marketplace._run_listing_script", new=AsyncMock()) as listing_mock,
    ):
        result = asyncio.run(
            sell_agent(
                outcome="test",
                creator_email="a@b.com",
                creator_name="A",
                gateway=cast(Any, None),
                model="m",
                agents_dir=cast(Any, None),
                catalog=cast(Any, None),
                auth=cast(Any, None),
            )
        )
        listing_mock.assert_not_called()
        ok = result.verified is False and result.listed is False
        return (
            ok,
            f"verified={result.verified} listed={result.listed} "
            f"listing_called={listing_mock.called}",
        )


def _case_verified_gets_listed() -> tuple[bool, str]:
    from madras.compiler.marketplace import sell_agent

    with (
        patch(
            "madras.compiler.marketplace.compile_to_residency",
            new=AsyncMock(return_value=_fake_residency(verified=True)),
        ),
        patch(
            "madras.compiler.marketplace._run_listing_script",
            new=AsyncMock(return_value={"seller_id": "s1", "product_id": "p1", "offer_id": "o1"}),
        ) as listing_mock,
    ):
        result = asyncio.run(
            sell_agent(
                outcome="test",
                creator_email="a@b.com",
                creator_name="A",
                gateway=cast(Any, None),
                model="m",
                agents_dir=cast(Any, None),
                catalog=cast(Any, None),
                auth=cast(Any, None),
            )
        )
        ok = (
            result.verified is True
            and result.listed is True
            and result.seller_id == "s1"
            and listing_mock.called
        )
        return ok, f"verified={result.verified} listed={result.listed} seller_id={result.seller_id}"


def _case_verified_but_listing_fails_distinctly() -> tuple[bool, str]:
    from madras.compiler.marketplace import sell_agent

    with (
        patch(
            "madras.compiler.marketplace.compile_to_residency",
            new=AsyncMock(return_value=_fake_residency(verified=True)),
        ),
        patch(
            "madras.compiler.marketplace._run_listing_script",
            new=AsyncMock(side_effect=RuntimeError("backend down")),
        ),
    ):
        result = asyncio.run(
            sell_agent(
                outcome="test",
                creator_email="a@b.com",
                creator_name="A",
                gateway=cast(Any, None),
                model="m",
                agents_dir=cast(Any, None),
                catalog=cast(Any, None),
                auth=cast(Any, None),
            )
        )
        # verified=True but listed=False is a DISTINCT state from the unverified case —
        # the agent genuinely passed, it's the listing backend that failed.
        ok = (
            result.verified is True and result.listed is False and "listing failed" in result.reason
        )
        return ok, f"verified={result.verified} listed={result.listed} reason={result.reason!r}"


_EXECUTORS: dict[str, Any] = {
    "unverified_never_lists": _case_unverified_never_lists,
    "verified_gets_listed": _case_verified_gets_listed,
    "verified_but_listing_fails_distinctly": _case_verified_but_listing_fails_distinctly,
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
        "suite_id": "marketplace_gate_conformance",
        "benchmark_family": "marketplace_gate_conformance",
        "features": _FEATURES,
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "gate_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class MarketplaceGateConformanceSuite(Suite):
    id: str = "marketplace_gate_conformance"
    name: str = "Marketplace gate conformance — deterministic"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM, zero subprocess) — mocks "
        "compile_to_residency + _run_listing_script and drives the REAL sell_agent gating "
        "logic. Fills the Marketplace subsystem gap surfaced s42 (was never tracked in "
        "suite_subsystem.py at all)."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))

    def load_cases(self) -> list[Case]:
        return [
            Case(
                id=f"marketplace_gate_conformance-{case_id}",
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
