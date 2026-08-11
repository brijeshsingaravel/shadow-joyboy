"""Agent-managed core memory — Letta/MemGPT self-editing blocks, governed (B68).

The paradigm our nightly/external Memory Manager lacked: **in-loop self-edit**. Core memory is a
small set of **labeled, size-bounded blocks** that live in the context window (like RAM); the agent
edits them DURING its reasoning loop via `append` / `replace` / `rethink` (Letta's
core_memory_append / core_memory_replace / memory_rethink). Conventional blocks: `persona` (the
agent's self-description) + `human` (what it knows about the user); custom blocks for task/project
state. The Madras edge: every self-edit is **bounded** (overflow is REJECTED, never silently
truncated — the agent must compact, no data loss) and **audited**. `LettaBackend` is the injectable
adapter for Letta's full runtime (archival + recall tiers + sleep-time consolidation), behind our
interface; our `CoreMemory` is the spine, the backend is swappable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryBlock:
    label: str
    value: str = ""
    limit: int = 2000  # char cap (Letta blocks are size-limited + configurable)
    description: str = ""

    @property
    def used(self) -> int:
        return len(self.value)

    @property
    def full(self) -> bool:
        return self.used >= self.limit

    def render(self) -> str:
        return f"<{self.label}>\n{self.value}\n</{self.label}>"


@dataclass
class BlockEdit:
    ok: bool
    block: str
    op: str  # append | replace | rethink | add
    reason: str = ""


@dataclass
class CoreMemory:
    blocks: dict[str, MemoryBlock] = field(default_factory=dict[str, MemoryBlock])
    audit: Callable[[dict[str, Any]], None] | None = None

    @classmethod
    def default(cls, audit: Callable[[dict[str, Any]], None] | None = None) -> CoreMemory:
        """The conventional persona + human blocks (Letta convention)."""
        return cls(
            blocks={
                "persona": MemoryBlock("persona", "", 1500, "the agent's self-description"),
                "human": MemoryBlock("human", "", 2000, "what the agent knows about the user"),
            },
            audit=audit,
        )

    def _audit(self, op: str, label: str, ok: bool, reason: str) -> None:
        if self.audit is not None:
            self.audit(
                {"event": "core_memory", "op": op, "block": label, "ok": ok, "reason": reason}
            )

    def _edit(self, label: str, op: str, candidate: str) -> BlockEdit:
        b = self.blocks.get(label)
        if b is None:
            e = BlockEdit(False, label, op, f"unknown block '{label}' (add_block first)")
            self._audit(op, label, False, e.reason)
            return e
        if len(candidate) > b.limit:
            e = BlockEdit(
                False,
                label,
                op,
                f"would exceed block limit ({len(candidate)}>{b.limit}) — compact first",
            )
            self._audit(op, label, False, e.reason)
            return e
        b.value = candidate
        self._audit(op, label, True, "")
        return BlockEdit(True, label, op)

    def add_block(self, label: str, *, limit: int = 2000, description: str = "") -> BlockEdit:
        if label in self.blocks:
            return BlockEdit(False, label, "add", "block already exists")
        self.blocks[label] = MemoryBlock(label, "", limit, description)
        self._audit("add", label, True, "")
        return BlockEdit(True, label, "add")

    def append(self, label: str, text: str) -> BlockEdit:
        b = self.blocks.get(label)
        if b is None:
            return self._edit(label, "append", text)  # routes to unknown-block error
        sep = "\n" if b.value else ""
        return self._edit(label, "append", f"{b.value}{sep}{text}")

    def replace(self, label: str, old: str, new: str) -> BlockEdit:
        b = self.blocks.get(label)
        if b is None:
            return self._edit(label, "replace", new)
        if old not in b.value:
            e = BlockEdit(False, label, "replace", f"old text not found in '{label}'")
            self._audit("replace", label, False, e.reason)
            return e
        return self._edit(label, "replace", b.value.replace(old, new, 1))

    def rethink(self, label: str, new_value: str) -> BlockEdit:
        """Rewrite the whole block (memory_rethink) — used by self-edit + sleep-time compaction."""
        return self._edit(label, "rethink", new_value)

    def render(self) -> str:
        """The in-context block string injected into the prompt (the 'RAM')."""
        return "\n\n".join(b.render() for b in self.blocks.values())


class LettaBackend:
    """Adapter over Letta (letta-ai/letta, Apache-2.0) for the full stateful runtime — archival
    (vector/disk) + recall (history) tiers + the sleep-time consolidation agent. Client injected
    (or a fake in tests); `connect()` lazy-imports `letta`. Our `CoreMemory` is the in-loop
    self-edit spine; this backend adds the deeper tiers + idle consolidation, swappable."""

    name = "letta"

    def __init__(self, client: Any) -> None:
        self._client = client

    @classmethod
    def connect(cls, client_factory: Callable[[], Any] | None = None) -> LettaBackend:
        if client_factory is not None:
            return cls(client_factory())
        try:
            import letta  # noqa: F401  # type: ignore[reportMissingImports, reportUnusedImport]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Letta backend needs `pip install letta` (Apache-2.0); point its LLM at LiteLLM "
                "for a zero-cost stateful runtime + sleep-time consolidation"
            ) from exc
        raise RuntimeError("provide a configured Letta client via client_factory")

    async def archival_search(self, query: str, *, k: int = 6) -> list[str]:
        return await self._client.archival_memory_search(query, limit=k)

    async def consolidate(self, core: CoreMemory) -> CoreMemory:
        """Sleep-time compute: let the backend rewrite (rethink) blocks from accumulated experience
        when idle — the in-loop analogue of our nightly Memory Manager."""
        current = {lbl: b.value for lbl, b in core.blocks.items()}
        rewrites: dict[str, str] = await self._client.sleeptime_rethink(current)
        for label, new_value in (rewrites or {}).items():
            core.rethink(label, new_value)
        return core
