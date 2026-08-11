"""Transaction-scoped tenant context for RLS (D83).

RLS reads the tenant from a session variable, so **how that variable is set IS the security
boundary**. Setting it session-scoped on a pooled connection is a cross-tenant read that looks
entirely ordinary: the backend that served tenant A is handed to tenant B milliseconds later, still
carrying A's context. It is close to invisible in development, where one developer on one
connection always gets the right value back.

So the variable is set with `set_config(..., is_local=True)` -- the function form of `SET LOCAL` --
**inside an explicit transaction**. Postgres reverts it at commit AND at rollback, so it cannot
survive onto a recycled connection. This is the pattern Supabase/Supavisor and PgBouncer require
for RLS in transaction pooling mode, adopted rather than invented.

**Why this helper opens the transaction itself.** `SET LOCAL` outside a transaction is a documented
no-op that merely emits a warning, and the symptom is RLS finding no tenant and returning zero
rows. Fail-closed, which is the right direction -- but silent, which is the hard kind to diagnose.
Leaving that to the caller would make every call site a place to get it wrong, which is the exact
class of defect D83 exists to remove.

Usage::

    async with pool.acquire() as conn:
        async with tenant_scope(conn, tenant):
            rows = await conn.fetch("SELECT * FROM madras_memory")   # policy-filtered

Not yet wired into the fabric: D83's cutover (the app connecting as `madras_app`) comes first, and
policies after that. This is the piece that must exist BEFORE either, because it is the one way to
get RLS wrong that the s61 one-table proof did not cover.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

TENANT_SETTING = "madras.tenant"
"""The GUC every RLS policy reads: `USING (tenant = current_setting('madras.tenant', true))`.

Named here rather than repeated as a literal, so a policy and a caller cannot disagree about the
key -- a typo on either side yields zero rows rather than an error, which is exactly the silent
shape this module is built against.
"""


@asynccontextmanager
async def tenant_scope(conn: Any, tenant: str) -> AsyncGenerator[Any]:
    """Bind `tenant` to this connection for the duration of one transaction.

    Reverted automatically on BOTH commit and rollback, so a failure inside the body cannot leave
    the tenant behind for whoever checks the connection out next.

    An empty tenant is refused. `current_setting` would return "" and every policy predicate would
    be false, so the caller would silently see nothing -- fail-closed is the right response to an
    ACCIDENT, but asking for it explicitly is a bug and should say so rather than look like an
    empty database.
    """
    if not tenant:
        raise ValueError("tenant must be a non-empty string -- an empty tenant matches no rows")

    async with conn.transaction():
        # is_local=True is the whole point: the function form of SET LOCAL. Passing False here
        # would make the value session-scoped and leak it to the next tenant on this backend.
        await conn.execute("SELECT set_config($1, $2, true)", TENANT_SETTING, tenant)
        yield conn


__all__ = ["TENANT_SETTING", "tenant_scope"]
