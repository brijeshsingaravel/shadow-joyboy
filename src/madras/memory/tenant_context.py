"""The ambient tenant badge -- who the current unit of work belongs to (s63).

Shadow's front door knows who is talking. The code that WRITES memory sits several layers deeper,
inside the graph every run passes through, and does not. The alternative to this module was
threading a tenant argument down that whole path -- which means editing the hottest, most
load-bearing code in Madras, and leaves a permanent trap where any writer added later silently
drops the name by simply not knowing to carry it.

So the tenant is set ONCE at the boundary and read where a database connection is acquired. That
is the shape the published multi-tenancy guidance settles on: set at the boundary, read at
session/connection acquisition, never in arbitrary query functions -- so the rule lives in one
place instead of depending on every future call site remembering it.

Follows the house ContextVar pattern (tools/canon_context.py, tools/background_job_context.py,
tools/browser_session.py, tools/builtin/delegate.py) rather than inventing a fifth style.

**THE LEAK THIS IS SHAPED AROUND.** Connection pools outlive requests: a tenant SET on a pooled
connection and not cleared is inherited by whoever is served next on that connection -- one
person's memory written under another person's name, which is the precise disaster the RLS work
exists to prevent. Two properties guard it here:

  * `tenant_scope()` restores the previous value in a `finally`, so an exception mid-request
    cannot leave the badge behind; and
  * at the database layer, asyncpg's `RESET ALL` on release wipes connection state between
    acquires. CLAUDE.md records that behaviour as a BUG that cost a session (it is why session
    state must be bound in `create_pool(setup=...)` and never `init=`). Used deliberately, the
    same wipe is what stops a tenant leaking to the next user of that connection.

**FAIL-CLOSED, DELIBERATELY.** `require_tenant()` raises; it does not fall back. The tempting
alternative is a "default" tenant -- and madras_memory already holds 8 dev rows under
`tenant = 'default'`. If that were the miss-behaviour, a broken boundary would quietly pile every
real person's memory under one fake identity, look completely healthy, and be discovered only by
someone asking why their memories were not theirs.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar


class MissingTenant(RuntimeError):
    """No tenant is bound for the current unit of work.

    Raised instead of returning a fallback: a write with no owner must stop, because a write
    with a GUESSED owner is worse than no write at all -- it is wrong data that looks right.
    """


# No `default=` beyond None, and None is not a usable tenant: an unset badge must be an error at
# the point of use, never a value that quietly satisfies a query.
_active: ContextVar[str | None] = ContextVar("madras_tenant", default=None)


def _clean(tenant: str) -> str:
    cleaned = tenant.strip()
    if not cleaned:
        # An empty string is falsy in Python but a perfectly storable SQL value -- it would
        # become a real tenant that owns rows and belongs to nobody.
        raise MissingTenant("tenant must be a non-empty string")
    return cleaned


def set_tenant(tenant: str) -> None:
    """Bind the tenant for the current context. Prefer `tenant_scope()`, which also unbinds."""
    _active.set(_clean(tenant))


def get_tenant() -> str | None:
    """The bound tenant, or None. For callers that legitimately tolerate absence."""
    return _active.get()


def require_tenant() -> str:
    """The bound tenant, or raise. The form the data layer should use."""
    tenant = _active.get()
    if tenant is None:
        raise MissingTenant(
            "no tenant bound for this request -- refusing rather than assuming an owner"
        )
    return tenant


@contextmanager
def tenant_scope(tenant: str) -> Generator[str]:
    """Bind `tenant` for the duration of the block, then restore whatever was bound before.

    The reset token is essential, not tidiness: `set()` alone would leave the value bound to the
    context after the block, and in a worker serving request after request that is the badge left
    on the desk. `finally` so an exception cannot skip the restore.
    """
    cleaned = _clean(tenant)
    token = _active.set(cleaned)
    try:
        yield cleaned
    finally:
        _active.reset(token)


__all__ = [
    "MissingTenant",
    "get_tenant",
    "require_tenant",
    "set_tenant",
    "tenant_scope",
]
