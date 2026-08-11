"""Identity & Privilege Boundary — the deterministic conformance suite (C1, framework-10x Part C).

Unlike every other suite here, this one does not run an LLM at all. The 5 s33 capabilities it
proves (`ExecutionGuard` / `authorize_request` / `CredentialBroker` / `EntitlementResolver` /
`verify_inbound`) are pure, injectable security primitives — "does a forged ticket get rejected"
is a property of the CODE, not of what an agent says. So each case is an adversarial (or legitimate
happy-path) direct call into the real module, asserted deterministically. Zero LLM tokens spent —
genuinely the most zero-cost suite in the roster.

Composes the EXISTING engine, doesn't extend it: reuses the same `Scenario`-shaped JSON + the
public/held_out partition convention (`suites/identity_boundary/data/{public,held_out}.json`), and
plugs into the sweep engine at the exact point 10+ external suites already use — `Suite.run()`
returning rows shaped for `sweep._external_scenario_row` (see `sweep.py`). `kind="external"` because
this suite fully self-drives (no `load_cases()`/judge/model involved).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite
from madras.factory.dynamic import (
    AuthContext,
    EntitlementPolicy,
    EntitlementResolver,
    RuleEntitlementPolicy,
)
from madras.messaging.inbound_verify import GENERIC, GITHUB, SLACK, sign, verify_inbound
from madras.security.approval_doctrine import ApprovalTicket, ExecutionGuard, sign_ticket
from madras.security.cred_broker import CredentialBroker
from madras.security.net_policy import NetPolicy
from madras.security.route_auth import RouteRegistry, authorize_request

DATA_DIR = Path(__file__).resolve().parent / "identity_boundary" / "data"
_FEATURES = [
    "approval_doctrine",
    "fail_closed_route_auth",
    "credential_brokering",
    "dynamic_capability_resolution",
    "inbound_signature_verification",
]
_SECRET = "test-secret-not-real"


def _load_cases(partition: str | None) -> list[dict[str, Any]]:
    files = {
        "public": [DATA_DIR / "public.json"],
        "held_out": [DATA_DIR / "held_out.json"],
        None: [DATA_DIR / "public.json", DATA_DIR / "held_out.json"],
    }[partition]
    rows: list[dict[str, Any]] = []
    for f in files:
        if f.exists():
            rows.extend(json.loads(f.read_text(encoding="utf-8")))
    return rows


# ---------------------------------------------------------------------------
# Per-module executors — each runs the REAL security code against the case's
# adversarial (or legitimate) setup and returns (passed, detail).
# ---------------------------------------------------------------------------


def _exec_approval_doctrine(setup: dict[str, Any]) -> tuple[bool, str]:
    ticket = ApprovalTicket(
        action="destructive.delete",
        tenant=setup["tenant"],
        idempotency_key=setup["idempotency_key"],
        approved=setup["approved"],
    )
    signature = sign_ticket(ticket, _SECRET) if setup["sign"] else "forged-signature"
    guard = ExecutionGuard(authorizer=lambda _a, _auth: setup["authorizer_ok"], secret=_SECRET)
    current_auth = AuthContext(tenant=setup["auth_tenant"])

    if setup["attack"] == "replay":
        first = guard.authorize(ticket, current_auth=current_auth, signature=signature)
        second = guard.authorize(ticket, current_auth=current_auth, signature=signature)
        ok = first.allow is True and second.allow is False
        return ok, f"first.allow={first.allow} second.allow={second.allow}"

    verdict = guard.authorize(ticket, current_auth=current_auth, signature=signature)
    expect_allow = setup.get("attack") == "valid_ticket"
    return verdict.allow is expect_allow, f"allow={verdict.allow} reason={verdict.reason!r}"


def _exec_route_auth(setup: dict[str, Any]) -> tuple[bool, str]:
    registry = RouteRegistry()
    if setup["register"] == "require_auth":
        registry.require_auth(setup["path"])
    elif setup["register"] == "allow_anonymous":
        registry.allow_anonymous(setup["path"])
    # "register": null -> leave unregistered (the unregistered-path-denies case)

    principal = {"tenant": setup["principal_tenant"]} if setup.get("principal_tenant") else None
    outcome = authorize_request(
        registry,
        setup["path"],
        principal=principal,
        env=setup.get("env", "prod"),
        placeholder=setup.get("placeholder", False),
    )
    expect_allow = setup["attack"] in {"authenticated", "explicit_anonymous", "placeholder_dev"}
    return outcome.allowed is expect_allow, f"allowed={outcome.allowed} reason={outcome.reason!r}"


def _exec_cred_broker(setup: dict[str, Any]) -> tuple[bool, str]:
    net_policy = NetPolicy(deny_domains=tuple(setup.get("deny_domains", ())))
    calls = {"n": 0}

    def resolver() -> str:
        calls["n"] += 1
        return "super-secret-token"

    broker = CredentialBroker(net_policy=net_policy)
    broker.register(setup["register_domain"], setup["register_header"], resolver=resolver)
    original_headers = {"X-Sandbox": "1"}
    result = broker.forward(setup["url"], dict(original_headers))

    attack = setup["attack"]
    if attack == "matching_domain":
        ok = result.injected and setup["register_header"] in result.upstream_headers
        return ok, f"injected={result.injected} headers={list(result.upstream_headers)}"
    if attack == "non_matching_domain":
        ok = not result.injected and setup["register_header"] not in result.upstream_headers
        return ok, f"injected={result.injected}"
    if attack == "input_not_mutated":
        ok = result.injected and original_headers == {"X-Sandbox": "1"}
        return ok, f"original_headers_after_call={original_headers}"
    if attack == "egress_blocked":
        ok = result.blocked and calls["n"] == 0
        return ok, f"blocked={result.blocked} resolver_calls={calls['n']}"
    return False, f"unknown attack {attack!r}"


def _exec_dynamic(setup: dict[str, Any]) -> tuple[bool, str]:
    attack = setup["attack"]
    if attack == "delegate_clips":
        resolver = EntitlementResolver(policy=lambda _auth: set())
        out = resolver.delegate(granted=setup["granted"], subset=setup["requested_subset"])
        ok = out == ["web"]
        return ok, f"delegated={out}"

    if attack == "union_grants":
        policy = RuleEntitlementPolicy(
            plan_caps={k: set(v) for k, v in setup["plan_caps"].items()},
            role_caps={k: set(v) for k, v in setup["role_caps"].items()},
            flag_caps={k: set(v) for k, v in setup["flag_caps"].items()},
        )
        auth = AuthContext(
            tenant="acme",
            plan=setup["plan"],
            roles=frozenset(setup["roles"]),
            flags=frozenset(setup["flags"]),
        )
    else:
        policy: EntitlementPolicy = lambda _auth: set(setup["entitled"])  # noqa: E731 — simple inline stand-in policy
        auth = AuthContext(tenant="acme")

    resolver = EntitlementResolver(policy=policy)
    resolved = resolver.resolve(
        declared_capabilities=setup["declared"], base_instructions=[], auth=auth
    )
    declared, entitled = set(setup["declared"]), set(policy(auth))

    if attack == "no_escalation":
        ok = set(resolved.capabilities) == (declared & entitled)
        return ok, f"effective={resolved.capabilities}"
    if attack == "deny_by_default":
        ok = set(resolved.denied) == (declared - entitled)
        return ok, f"denied={resolved.denied}"
    if attack == "union_grants":
        ok = set(resolved.capabilities) == declared and not resolved.denied
        return ok, f"effective={resolved.capabilities} denied={resolved.denied}"
    return False, f"unknown attack {attack!r}"


_INBOUND_SCHEMES = {"github": GITHUB, "generic": GENERIC, "slack": SLACK}


def _exec_inbound_verify(setup: dict[str, Any]) -> tuple[bool, str]:
    channel = setup["channel"]
    scheme = _INBOUND_SCHEMES[channel]
    now = 1_700_000_000.0
    attack = setup["attack"]

    if attack == "missing_header":
        result = verify_inbound(
            channel,
            body=setup["body"],
            headers={},
            secret=setup["secret"],
            principal=setup["configured_principal"],
            now=now,
        )
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"

    if attack == "malformed_timestamp":
        sig = sign(SLACK, body=setup["body"], secret=setup["secret"], ts="0")
        headers = {SLACK.header: sig, SLACK.timestamp_header: "not-a-number"}
        result = verify_inbound(
            channel,
            body=setup["body"],
            headers=headers,
            secret=setup["secret"],
            principal=setup["configured_principal"],
            now=now,
        )
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"

    if attack == "replay_outside_skew":
        ts_value = now - setup["ts_skew_seconds"]
        sig = sign(SLACK, body=setup["body"], secret=setup["secret"], ts=str(ts_value))
        headers = {SLACK.header: sig, SLACK.timestamp_header: str(ts_value)}
        result = verify_inbound(
            channel,
            body=setup["body"],
            headers=headers,
            secret=setup["secret"],
            principal=setup["configured_principal"],
            now=now,
            max_skew=setup["max_skew"],
        )
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"

    # valid_signature / tampered_body — sign the ORIGINAL body; verify a (possibly mutated) body.
    sch = scheme
    signed_over = sch.template.format(body=setup["body"], ts="")
    sig = sign(sch, body=setup["body"], secret=setup["secret"])
    final_body = setup["body"] + " TAMPERED" if setup.get("tamper") else setup["body"]
    headers = {sch.header: sig}
    result = verify_inbound(
        channel,
        body=final_body,
        headers=headers,
        secret=setup["secret"],
        principal=setup["configured_principal"],
        now=now,
    )
    if attack == "tampered_body":
        return result.ok is False, f"ok={result.ok} reason={result.reason!r}"
    # valid_signature
    ok = result.ok is True and result.principal == setup["configured_principal"]
    return ok, f"ok={result.ok} principal={result.principal!r} signed_over={signed_over!r}"


_EXECUTORS = {
    "approval_doctrine": _exec_approval_doctrine,
    "route_auth": _exec_route_auth,
    "cred_broker": _exec_cred_broker,
    "dynamic": _exec_dynamic,
    "inbound_verify": _exec_inbound_verify,
}


def run_case(row: dict[str, Any]) -> dict[str, Any]:
    """Execute one adversarial/happy-path case against the real security module. Pure, hermetic."""
    setup = row["setup"]
    executor = _EXECUTORS[setup["module"]]
    t0 = time.perf_counter()
    try:
        passed, detail = executor(setup)
    except Exception as exc:  # a raising module is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": row["id"],
        "suite_id": "identity_boundary_conformance",
        "benchmark_family": "identity_boundary_conformance",
        "features": row.get("features", []),
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "security_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class IdentityBoundaryConformanceSuite(Suite):
    id: str = "identity_boundary_conformance"
    name: str = "Identity & Privilege Boundary — deterministic conformance"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct adversarial + "
        "happy-path calls into ExecutionGuard/authorize_request/CredentialBroker/"
        "EntitlementResolver/verify_inbound. Public + held_out partitions."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    # None = both partitions (the official Index view, mirrors NativeSuite's convention).
    partition: str | None = None

    def load_cases(self) -> list[Case]:
        """Lightweight coverage-stub Cases (one per module under test).

        This suite drives its own deterministic loop via `run()`, so these are not executed
        through the governed runner — they exist so the suite registry can aggregate coverage,
        matching the convention every other external suite (tau2/swebench/...) follows.
        """
        return [
            Case(
                id=f"identity_boundary-{module}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=[module],
                tools=[],
                prompt=f"{self.name}: {module} conformance cases (external; zero-LLM)",
            )
            for module in sorted(_EXECUTORS)
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        # Deterministic + zero-cost: model/concurrency are irrelevant (no LLM call at all).
        del model, k, concurrency
        return [run_case(row) for row in _load_cases(self.partition)]
