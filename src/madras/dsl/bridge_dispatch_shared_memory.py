"""Phase U -- the shared_memory transport's dispatcher: takes a real `BridgeManifest`
(transport=shared_memory) and actually calls it, reusing K-phase's own real, already-proven
mechanism (`kollan_allocator.BumpAllocator` + `kollan_sparse_radix.SparseRadixIndex`) -- a
Morton-banded, arena-resident radix tree -- exactly the same "reuse, don't reinvent" move
`dispatch_in_process`/`dispatch_network` already made for their own transports.

**Honest v0 scope, disclosed, not glossed over:** no real "call a function over shared memory"
mechanism exists anywhere in this codebase yet -- only K-phase's real key/value store. So this
dispatcher is a governed PUT/GET over that store, not an RPC-style call: `dispatch_shared_memory`
with a `value` PUTS it at `key` (and returns it back, read from the store, not the live Python
value -- proving the round trip actually went through real memory); without a `value` it GETS
whatever is currently at `key` (or `None` if nothing's been put there yet). A genuine future call
mechanism (the Phase-P dimensional-band vCPU fabric actually executing code, not just storing
values) is real, separate future work.

One region (one `BumpAllocator` + `SparseRadixIndex` pair) per manifest `name`, lazily created on
first dispatch and kept alive for the process's lifetime -- "shared" here means shared ACROSS
CALLS within this process (matching K-phase's own hosted, not-yet-cross-process scope), not yet
shared across OS processes.
"""

from __future__ import annotations

from madras.dsl.kollan_allocator import BumpAllocator
from madras.dsl.kollan_sparse_radix import SparseRadixIndex
from madras.models.bridge_manifest import BridgeManifest, Transport

_DEFAULT_CAPACITY = 1 << 20  # 1 MiB -- generous for a v0 KV region, real future work to size

_regions: dict[str, SparseRadixIndex] = {}


def _region_for(manifest: BridgeManifest) -> SparseRadixIndex:
    iface = manifest.shared_memory_interface
    assert iface is not None  # enforced by BridgeManifest's own transport/interface validator
    index = _regions.get(manifest.name)
    if index is None:
        allocator = BumpAllocator(_DEFAULT_CAPACITY)
        index = SparseRadixIndex(allocator, iface.region.key_bits)
        _regions[manifest.name] = index
    return index


def dispatch_shared_memory(
    manifest: BridgeManifest, key: int, value: int | None = None
) -> int | None:
    """PUT `value` at `key` (returning it back, re-read from the real region) if `value` is
    given, else GET whatever is currently stored at `key` (`None` if nothing is)."""
    if manifest.transport is not Transport.SHARED_MEMORY:
        raise ValueError(
            f"dispatch_shared_memory only handles transport=shared_memory, "
            f"got {manifest.transport!r}"
        )
    index = _region_for(manifest)
    if value is not None:
        index.insert(key, value)
    return index.lookup(key)


__all__ = ["dispatch_shared_memory"]
