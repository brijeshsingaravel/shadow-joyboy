"""Cockpit-endpoint (API-level) probe — the customer-facing sections.

Scenarios test Shadow's brain; this tests the PRODUCT surface: every customer-facing
endpoint returns the right status and response shape (keys), so a section can't
silently break. Driven via FastAPI TestClient (no network). Correctness of deep
behaviour stays with the agent scenarios; this is "is the section wired and shaped".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, cast


@dataclass
class EndpointCase:
    method: str  # GET | POST
    path: str
    body: dict[str, Any] | None = None
    expect_status: tuple[int, ...] = (200,)
    expect_keys: list[str] = field(default_factory=list[str])  # top-level keys in JSON
    label: str = ""


@dataclass
class ProbeResult:
    path: str
    ok: bool  # request returned (no transport error)
    passed: bool  # status + shape held
    status: int
    detail: str


def run_endpoint_case(case: EndpointCase, client: Any) -> ProbeResult:
    """Issue one request via a TestClient and assert status + response shape."""
    try:
        if case.method.upper() == "POST":
            resp = client.post(case.path, json=case.body or {})
        else:
            resp = client.get(case.path)
    except Exception as exc:
        return ProbeResult(
            case.path,
            ok=False,
            passed=False,
            status=0,
            detail=f"transport error: {type(exc).__name__}: {exc}",
        )
    status_ok = resp.status_code in case.expect_status
    shape_ok = True
    detail = f"status {resp.status_code}"
    if status_ok and case.expect_keys:
        try:
            data: Any = resp.json()
            obj: Any = data
            if isinstance(data, list) and data:
                obj = cast("list[Any]", data)[0]
            missing = [k for k in case.expect_keys if not (isinstance(obj, dict) and k in obj)]
            shape_ok = not missing
            if missing:
                detail = f"missing keys {missing}"
        except Exception as exc:
            shape_ok = False
            detail = f"non-JSON body: {exc}"
    return ProbeResult(
        case.path, ok=True, passed=(status_ok and shape_ok), status=resp.status_code, detail=detail
    )


def run_probe_suite(cases: list[EndpointCase], client: Any) -> list[ProbeResult]:
    return [run_endpoint_case(c, client) for c in cases]


def readiness_pct(results: list[ProbeResult]) -> float:
    if not results:
        return 0.0
    return round(sum(1 for r in results if r.passed) / len(results), 4)
