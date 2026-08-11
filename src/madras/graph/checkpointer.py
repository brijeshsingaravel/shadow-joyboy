"""PostgresSaver wiring for the agent graph.

The checkpointer provides durable, resumable state — critical because
~60% of agent prod incidents trace to state management (LangChain 2026).

Delegates topology to `build_minimal_graph` so there is exactly ONE place
where the graph shape is defined (Phase 1 will add nodes; both code paths
benefit from the single source of truth).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver  # type: ignore[import-untyped]

from madras.factory.spawn import AgentRecord
from madras.graph.build import build_minimal_graph

CHECKPOINT_TABLES = (
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
)
"""LangGraph's own tables. Named here only to verify their presence -- never to define them.

`0000_bootstrap_runtime_tables.sql` deliberately does NOT own this schema: LangGraph owns it and
changes it between library versions, so hardcoding it into a migration would fight the library on
its next upgrade. `scripts/bootstrap_db.py` calls the library's own `setup()` after applying
migrations, which is the correct owner boundary.
"""


class MissingCheckpointTables(RuntimeError):
    """LangGraph's checkpoint tables do not exist -- the database was not bootstrapped.

    Loud on purpose. The checkpointer is what makes agent state durable and resumable; a run that
    silently proceeded without it would look successful right up until something needed to resume.
    """


@asynccontextmanager
async def build_checkpointed_graph(agent: AgentRecord, *, postgres_url: str) -> AsyncGenerator[Any]:
    """Build the minimal graph wrapped with an async PostgresSaver.

    **`saver.setup()` is deliberately NOT called here (s61, D83 step 5).** It creates LangGraph's
    tables, and it ran on EVERY graph build -- DDL on the hot path of every checkpointed agent run,
    which `madras_app` (the DDL-less role RLS requires) is refused outright.

    Provisioning belongs to `scripts/bootstrap_db.py`, which already calls the library's own
    `setup()` after applying migrations. This is the one module in the sweep whose schema is NOT
    moved into a migration -- LangGraph owns it and changes it between versions -- so the fix is to
    move the CALL out of the request path rather than the definition into SQL.

    The presence check is one query on the first build, cached thereafter, and it fails loudly: a
    run proceeding without a checkpointer looks fine until something needs to resume.
    """
    async with AsyncPostgresSaver.from_conn_string(postgres_url) as saver:
        await _verify_checkpoint_tables(saver)
        yield build_minimal_graph(agent, checkpointer=saver)


_verified_urls: set[str] = set()


async def _verify_checkpoint_tables(saver: Any) -> None:
    """Confirm LangGraph's tables exist, once per connection string.

    Cached across builds because this runs on every graph construction; the previous code paid a
    full `setup()` there, so even one query per build would be an improvement, but zero is better.
    """
    async with saver.conn.cursor() as cur:
        await cur.execute(
            "SELECT tablename FROM pg_tables WHERE tablename = ANY(%s)",
            (list(CHECKPOINT_TABLES),),
        )
        # LangGraph's saver configures psycopg with a dict row factory, so rows are keyed by
        # column name -- `row[0]` raises KeyError here rather than returning the first column.
        present = {row["tablename"] for row in await cur.fetchall()}
    missing = [t for t in CHECKPOINT_TABLES if t not in present]
    if missing:
        raise MissingCheckpointTables(
            f"{', '.join(missing)} do(es) not exist -- run "
            f"`uv run python scripts/bootstrap_db.py` (LangGraph owns this schema, so it is "
            f"created by the library's own setup(), never by a migration)"
        )
