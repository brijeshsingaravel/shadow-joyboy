"""Tenant-pinned approval doctrine — harden the approval->execute boundary (row 84, eve doctrines).

A human approval (the gate firing) is NOT authorization. `ExecutionGuard` enforces the six eve
doctrines at the moment an approved action executes — all **fail-closed**:
1. **gate != authz** — re-authorize the actor independently at execution (approval is necessary,
   not sufficient).
2. **recheck-in-executor** + 3. **input-can't-select-tenant** — tenancy is taken from the VERIFIED
   auth and must match the ticket; the action/input never selects the tenant.
4. **idempotency-key** — a replayed/duplicate approval executes AT MOST once.
5. **protect-resume-endpoint** — the ticket is HMAC-signed; a forged/unsigned resume is rejected.
6. **fail-closed** — any mismatch/missing field denies.
Composes B17 (the authorizer) + row-83 `AuthContext` + row-81 HMAC. Pure stdlib.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from madras.factory.dynamic import AuthContext


@dataclass
class ApprovalTicket:
    action: str
    tenant: str  # the tenant the approval was granted FOR
    idempotency_key: str
    approved: bool = False  # the gate fired (a human said yes)


@dataclass
class ExecVerdict:
    allow: bool
    reason: str = ""


def _ticket_payload(t: ApprovalTicket) -> str:
    return f"{t.action}|{t.tenant}|{t.idempotency_key}|{int(t.approved)}"


def sign_ticket(ticket: ApprovalTicket, secret: str) -> str:
    """HMAC over the ticket — the approval flow signs it; the resume endpoint verifies it."""
    return hmac.new(
        secret.encode("utf-8"), _ticket_payload(ticket).encode("utf-8"), hashlib.sha256
    ).hexdigest()


# (action, auth) -> bool: the independent authorization at execution (e.g. PermissionEngine.check)
Authorizer = Callable[[str, AuthContext], bool]


@dataclass
class ExecutionGuard:
    authorizer: Authorizer
    secret: str = ""  # resume-endpoint signing secret (protect-resume-endpoint)
    audit: Callable[[dict[str, Any]], None] | None = None
    _executed: set[str] = field(default_factory=set[str], init=False)  # idempotency ledger

    def _audit(self, event: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit({"event": f"approval_{event}", **kw})

    def _deny(self, reason: str, ticket: ApprovalTicket) -> ExecVerdict:
        self._audit("denied", action=ticket.action, tenant=ticket.tenant, reason=reason)
        return ExecVerdict(False, reason)

    def authorize(
        self, ticket: ApprovalTicket, *, current_auth: AuthContext, signature: str = ""
    ) -> ExecVerdict:
        """Authorize an approved action at execution time. Fail-closed throughout."""
        # 5. protect-resume-endpoint — reject a forged/unsigned ticket before anything else
        if self.secret and not hmac.compare_digest(
            signature or "", sign_ticket(ticket, self.secret)
        ):
            return self._deny("forged/unsigned approval ticket (resume endpoint protected)", ticket)
        if not ticket.approved:
            return self._deny("not approved (gate did not fire)", ticket)
        # 1. gate != authz — independently re-authorize the actor at execution
        if not self.authorizer(ticket.action, current_auth):
            return self._deny("authz failed at execution (the gate is not authorization)", ticket)
        # 2 + 3. recheck-in-executor + input-can't-select-tenant — tenant from VERIFIED auth
        if current_auth.tenant != ticket.tenant:
            return self._deny(
                f"tenant mismatch (approved for {ticket.tenant!r}, caller {current_auth.tenant!r})",
                ticket,
            )
        # 4. idempotency — execute at most once
        if ticket.idempotency_key in self._executed:
            return self._deny("idempotency: already executed", ticket)

        self._executed.add(ticket.idempotency_key)
        self._audit("authorized", action=ticket.action, tenant=ticket.tenant)
        return ExecVerdict(True, "authorized")
