"""kollan_recall.py -- T8.14: resolves Recall (memory-ref read) into a real address `compile_goal`
can read back via T8.13's `emit_load_absolute` -- zero new stencil bytes needed.

**The real design point.** There's no compute-substrate reason to route the memory-graph LOOKUP
itself through compiled machine code the way `capability-call` needs to: a lookup is I/O-bound
(hitting Ninaivu/Postgres/whatever), not something native execution speed helps with. Unboxing a
raw `PyObject*` int result inside hand-rolled machine code (chaining `PyLong_AsLong`/`Py_DecRef`
after `PyObject_CallObject`) would be real, fragile, multi-register-preserving machinery for no
actual benefit. Instead, this module calls each real provider directly in Python, writes the
result into a real memory cell, and hands `compile_goal` the cell's address -- the same
"resolved-address-in, bytes-out" shape `array_addresses`/`capability_addresses` already have. Only
the READ BACK of that value (by whatever the compiled goal does with it next -- a Branch, a
further Call) needs to be real machine code, and `emit_load_absolute` already provides that.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from tamil_lang.ast import Goal
from tamil_lang.kollan import collect_recalls


class UnresolvedRecall(RuntimeError):
    """Raised when a goal recalls a key with no registered provider callable."""


def resolve_recalls(
    goal: Goal, providers: dict[str, Callable[[], int]]
) -> tuple[dict[str, int], list[ctypes.c_int32]]:
    """For every distinct Recall key `collect_recalls(goal)` finds, call `providers[key]()`
    (a real Python callable, e.g. reading the real memory graph) and write its real int result
    into a real `ctypes.c_int32` cell. Returns `(recall_addresses, keepalive)`, ready to pass
    straight into `compile_goal`'s `recall_addresses` parameter -- `keepalive` is the list of live
    cells the caller MUST keep referenced for as long as the compiled code might run (the address
    is only valid while the cell itself is still alive)."""
    addresses: dict[str, int] = {}
    keepalive: list[ctypes.c_int32] = []
    for key in collect_recalls(goal):
        if key not in providers:
            raise UnresolvedRecall(f"no provider registered for recall({key!r})")
        cell = ctypes.c_int32(providers[key]())
        addresses[key] = ctypes.addressof(cell)
        keepalive.append(cell)
    return addresses, keepalive


__all__ = ["UnresolvedRecall", "resolve_recalls"]
