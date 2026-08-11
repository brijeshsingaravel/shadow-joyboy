"""kollan_bridge.py -- the real capability-address resolution Kollan was blocked on.

No Madras capability is a raw C-ABI function -- they're all live Python callables (tools,
built-ins, agent methods). `emit_capability_call`'s `target_addr` param only ever made sense
for something that already has a stable native address (an OS DLL export like
`GetCurrentProcessId`); resolving an arbitrary Python capability to one is impossible, because
Python callables aren't native code at all.

The real bridge: CPython's own C API. `PyObject_CallObject(callable, args)` is a genuine,
resolvable-at-runtime native function -- the SAME one CPython itself uses internally to invoke
any callable -- so native code compiled by Kollan can call it with a real Python object's own
address (`id(obj)`) and get a real result back, without needing that object to be C-ABI
anything. This is what makes Kollan able to compile a call to a REAL `.tamil` capability for
the first time, not just a synthetic test target.

**Two real, distinct bugs were found and fixed getting this to work, not glossed over:**
1. Windows x64 requires every JIT compiler to register real unwind metadata
   (`RtlAddFunctionTable`) for any code that calls something non-leaf -- omitting it was
   SILENTLY fatal (no exception, no traceback, the whole process just died) the instant
   `PyObject_CallObject`'s deep, non-leaf CPython-eval-loop code needed to unwind past our
   unregistered JIT frame. Leaf calls (`GetCurrentProcessId`) never triggered this, which is
   exactly why they worked without it. See `madras.dsl.kollan._build_unwind_info`.
2. `ctypes.CFUNCTYPE` releases the GIL for the duration of the call (it assumes long-running,
   GIL-agnostic C code); `PyObject_CallObject` requires the GIL held, since it calls back into
   the interpreter. `ctypes.PYFUNCTYPE` keeps the GIL held throughout AND automatically
   re-raises a real pending Python exception after the call -- better than a manual
   `PyErr_Print`-and-generic-error fallback, which is now only reached in the (so far
   unobserved) case where that auto-detection doesn't fire.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable

from tamil_lang.kollan import (
    emit_call_with_args,
    emit_frame_epilogue,
    emit_frame_prologue,
    emit_lea_local,
    emit_load_immediate64,
    emit_load_local64,
    emit_python_call,
    emit_store_local64,
    frame_size,
)

from madras.dsl.kollan import run_framed_call_with_unwind, run_python_call_raw

_RET_LEN_X86_64 = 1  # the single trailing 0xc3 byte every x86-64 stencil here ends in


class PythonCallFailed(RuntimeError):
    """A fallback signal for the case `ctypes.PYFUNCTYPE`'s own automatic pending-exception
    re-raise DOESN'T fire (result_ptr is NULL but no exception got propagated) -- in practice,
    a real Python exception raised inside the bridged call surfaces as itself (its own real
    type/message), not this class; this only exists so a NULL result is never silently treated
    as success."""


def python_call_object_address() -> int:
    """The real, resolved-at-runtime address of CPython's `PyObject_CallObject` in THIS running
    interpreter -- not a link-time constant (libpython's load address varies per process), but a
    genuinely fixed address for the lifetime of this process, exactly like `GetCurrentProcessId`
    is within one process's lifetime. Public (T9.2): `compile_goal`'s own `python_api_addr`
    parameter needs this same resolved address for a real `.tamil`-source `ffi_bridge` call, not
    just this module's own `call_python_object`."""
    ctypes.pythonapi.PyObject_CallObject.restype = ctypes.c_void_p
    ctypes.pythonapi.PyObject_CallObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    addr = ctypes.cast(ctypes.pythonapi.PyObject_CallObject, ctypes.c_void_p).value
    if addr is None:
        raise RuntimeError("could not resolve PyObject_CallObject's address")
    return addr


def _convert_result_ptr(result_ptr: int | None, description: str) -> object:
    """Convert a raw `PyObject*` result (or `None` for NULL) back into a real Python object,
    releasing the extra reference `PyObject_CallObject` returns a new one of. Shared by every
    caller here (`call_python_object`, `call_python_object_with_args`,
    `run_compiled_capability_call`) so the exact same conversion/refcount/exception-detection
    logic isn't duplicated between them."""
    if result_ptr is None:
        ctypes.pythonapi.PyErr_Print()
        raise PythonCallFailed(
            f"{description} raised inside CPython's C API (see the printed traceback above)"
        )
    result = ctypes.cast(result_ptr, ctypes.py_object).value
    ctypes.pythonapi.Py_DecRef(ctypes.py_object(result))
    return result


def _run_and_convert(code: bytes, description: str) -> object:
    """Execute a `PyObject_CallObject`-shaped stencil (T8.11's `emit_python_call`, or -- T9.2 --
    `compile_goal`'s own byte-identical output for a bare `ffi_bridge` `Call`) via the single-
    `sub rsp`, no-locals unwind path (`run_python_call_raw`) and convert its result. Shared by
    `call_python_object` (builds its own code) and `run_compiled_capability_call` (runs code
    `compile_goal` already built)."""
    return _convert_result_ptr(run_python_call_raw(code), description)


def _resolve_capi(name: str) -> int:
    """Any CPython C API function's real, resolved-at-runtime address in THIS running
    interpreter, needed by `call_python_object_with_args` to compose a real multi-step call
    sequence (build a tuple, box each arg, call, release the tuple) out of several distinct C
    API entry points, not just `PyObject_CallObject` alone.

    **Deliberately does NOT set `.argtypes`/`.restype`** on the resolved `ctypes.pythonapi.*`
    object (unlike `python_call_object_address`'s own pattern) -- a real bug found live, not
    assumed: `ctypes.pythonapi.*` function objects are PROCESS-GLOBAL and SHARED, so setting
    `Py_DecRef.argtypes = [c_void_p]` here to resolve its address silently broke the EXISTING,
    already-working plain-ctypes call `_convert_result_ptr` makes elsewhere
    (`ctypes.pythonapi.Py_DecRef(ctypes.py_object(result))`, which relies on ctypes' own
    automatic `py_object` marshaling) -- a `TypeError: wrong type` on a call site that hadn't
    changed at all. `ctypes.cast(fn, ctypes.c_void_p).value` needs no argtypes/restype set to
    resolve a real address; only actually CALLING through the ctypes-mediated Python path needs
    them, which this function never does."""
    fn = getattr(ctypes.pythonapi, name)
    addr = ctypes.cast(fn, ctypes.c_void_p).value
    if addr is None:
        raise RuntimeError(f"could not resolve {name}'s address")
    return addr


def call_python_object(callable_obj: Callable[[], object]) -> object:
    """Call a real, live, 0-arg Python callable through Kollan's native FFI bridge -- compiles
    a stencil that calls CPython's own `PyObject_CallObject(callable, NULL)` with the object's
    real address, executes it live, and converts the raw `PyObject*` result back into a real
    Python object. Not a simulation: the actual call happens through actual executed machine
    code, generated by our own emitter."""
    callable_ptr = id(callable_obj)  # valid for the duration of this call: `callable_obj` is a
    # live local reference the whole time the compiled code below actually runs.
    api_addr = python_call_object_address()
    code = emit_python_call("x86_64", callable_ptr, api_addr)
    return _run_and_convert(code, f"the native call to {callable_obj!r}")


def call_python_object_with_args(callable_obj: Callable[..., object], *args: int) -> object:
    """G8 -- `call_python_object`'s N-arg sibling: calls a real, live Python callable with N
    REAL integer arguments through Kollan's native FFI bridge, by composing several distinct
    CPython C API calls into ONE real multi-local stack frame (`emit_frame_prologue`, G8's own
    `run_framed_call_with_unwind` -- the isolated-and-verified unwind-info extension this whole
    mechanism depends on): `PyTuple_New(n)` -> box each arg via `PyLong_FromLongLong` ->
    `PyTuple_SetItem` (steals each boxed ref, no separate `Py_DecRef` needed per item) ->
    `PyObject_CallObject(callable, tuple)` -> `Py_DecRef(tuple)` (release OUR own reference to
    the tuple itself). Every C API call is a fragment built from the SAME `emit_call_with_args`
    already proven for native calls -- zero new call-site machinery, only composition. v0
    boundary: args are raw integers (`PyLong_FromLongLong`), matching every other v0 arg-passing
    boundary in this codebase (int is the base value type; strings/records aren't boxed here
    yet, real future work)."""
    if not args:
        return call_python_object(callable_obj)  # reuse the proven 0-arg path unchanged

    callable_ptr = id(callable_obj)
    tuple_new_addr = _resolve_capi("PyTuple_New")
    long_from_ll_addr = _resolve_capi("PyLong_FromLongLong")
    tuple_setitem_addr = _resolve_capi("PyTuple_SetItem")
    decref_addr = _resolve_capi("Py_DecRef")
    call_object_addr = python_call_object_address()

    n = len(args)
    tuple_slot, scratch1, scratch2 = n, n + 1, n + 2  # slots 0..n-1 hold the raw arg values
    n_slots = n + 3
    isa, abi = "x86_64", "win64"

    frags = [emit_frame_prologue(isa, n_slots)]
    for i, v in enumerate(args):
        frags += [emit_load_immediate64(isa, v), emit_store_local64(isa, i)]

    # tuple = PyTuple_New(n)
    frags += [
        emit_load_immediate64(isa, n),
        emit_store_local64(isa, scratch1),
        emit_call_with_args(isa, tuple_new_addr, [scratch1], abi)[:-_RET_LEN_X86_64],
        emit_store_local64(isa, tuple_slot),
    ]

    for i in range(n):
        # boxed = PyLong_FromLongLong(args[i]); PyTuple_SetItem(tuple, i, boxed) -- SetItem
        # steals the boxed reference, no separate Py_DecRef needed for it.
        frags += [
            emit_call_with_args(isa, long_from_ll_addr, [i], abi)[:-_RET_LEN_X86_64],
            emit_store_local64(isa, scratch1),
            emit_load_immediate64(isa, i),
            emit_store_local64(isa, scratch2),
            emit_call_with_args(isa, tuple_setitem_addr, [tuple_slot, scratch2, scratch1], abi)[
                :-_RET_LEN_X86_64
            ],
        ]

    # result = PyObject_CallObject(callable, tuple)
    frags += [
        emit_load_immediate64(isa, callable_ptr),
        emit_store_local64(isa, scratch2),
        emit_call_with_args(isa, call_object_addr, [scratch2, tuple_slot], abi)[:-_RET_LEN_X86_64],
        emit_store_local64(isa, scratch1),
        # Py_DecRef(tuple) -- release OUR OWN reference to the tuple (each item's own reference
        # was already stolen by PyTuple_SetItem above).
        emit_call_with_args(isa, decref_addr, [tuple_slot], abi)[:-_RET_LEN_X86_64],
        emit_load_local64(isa, scratch1),
        emit_frame_epilogue(isa, n_slots),
    ]
    code = b"".join(frags)
    result_ptr = run_framed_call_with_unwind(code, frame_size(isa, n_slots))
    return _convert_result_ptr(result_ptr, f"the native N-arg call to {callable_obj!r}")


def call_python_object_vectorcall(callable_obj: Callable[..., object], *args: int) -> object:
    """G8 -- the SECOND, fully parallel N-arg calling convention (founder-chosen scope, s56:
    build both, not just one): `PyObject_Vectorcall(callable, args, nargsf, kwnames)` (CPython
    3.9+, research: GPL-LLM-OSS Radar s56 -- the modern, JIT/FFI-embedder-friendly convention,
    avoiding `call_python_object_with_args`'s tuple allocation entirely). `args` is a real C
    array: `PyObject*` pointers boxed via `PyLong_FromLongLong`, stored into a CONTIGUOUS run of
    local slots and addressed by `emit_lea_local` (the array's own base pointer) -- ascending
    slot index is DESCENDING memory address (`_local_offset(slot) = -8*(slot+1)`), so `args[i]`
    is deliberately stored into slot `2n-1-i` (not slot `n+i`) to make ascending ARRAY index
    match ascending MEMORY address, the array's own real C-layout contract. Unlike
    `PyTuple_SetItem` (steals each item's reference), `Vectorcall`'s caller RETAINS ownership of
    the array elements -- each boxed arg is explicitly `Py_DecRef`'d after the call, not
    implicitly released."""
    if not args:
        return call_python_object(callable_obj)  # reuse the proven 0-arg path unchanged

    callable_ptr = id(callable_obj)
    vectorcall_addr = _resolve_capi("PyObject_Vectorcall")
    long_from_ll_addr = _resolve_capi("PyLong_FromLongLong")
    decref_addr = _resolve_capi("Py_DecRef")

    n = len(args)
    array_slots = [2 * n - 1 - i for i in range(n)]  # args[i] -> slot(2n-1-i), ascending address
    args_ptr_slot, callable_slot, n_slot, kwnames_slot = 2 * n, 2 * n + 1, 2 * n + 2, 2 * n + 3
    n_slots = 2 * n + 4
    isa, abi = "x86_64", "win64"

    frags = [emit_frame_prologue(isa, n_slots)]
    for i, v in enumerate(args):
        frags += [emit_load_immediate64(isa, v), emit_store_local64(isa, i)]

    for i in range(n):
        # boxed = PyLong_FromLongLong(args[i]) -> its own array position
        frags += [
            emit_call_with_args(isa, long_from_ll_addr, [i], abi)[:-_RET_LEN_X86_64],
            emit_store_local64(isa, array_slots[i]),
        ]

    frags += [
        # args_ptr = &slot(array_slots[0]) -- box[0]'s own slot, the array's LOWEST address
        emit_lea_local(isa, array_slots[0]),
        emit_store_local64(isa, args_ptr_slot),
        emit_load_immediate64(isa, callable_ptr),
        emit_store_local64(isa, callable_slot),
        emit_load_immediate64(isa, n),
        emit_store_local64(isa, n_slot),
        emit_load_immediate64(isa, 0),  # kwnames = NULL
        emit_store_local64(isa, kwnames_slot),
        emit_call_with_args(
            isa, vectorcall_addr, [callable_slot, args_ptr_slot, n_slot, kwnames_slot], abi
        )[:-_RET_LEN_X86_64],
        emit_store_local64(isa, callable_slot),  # stash the result (callable_ptr no longer needed)
    ]
    for i in range(n):
        # Vectorcall's caller RETAINS ownership of each array element -- release it explicitly,
        # unlike PyTuple_SetItem's implicit steal in call_python_object_with_args.
        frags.append(
            emit_call_with_args(isa, decref_addr, [array_slots[i]], abi)[:-_RET_LEN_X86_64]
        )
    frags += [emit_load_local64(isa, callable_slot), emit_frame_epilogue(isa, n_slots)]

    code = b"".join(frags)
    result_ptr = run_framed_call_with_unwind(code, frame_size(isa, n_slots))
    return _convert_result_ptr(result_ptr, f"the native Vectorcall to {callable_obj!r}")


def run_compiled_capability_call(code: bytes, frame_bytes: int = 0) -> object:
    """T9.2 -- runs `compile_goal`'s OWN output for a goal containing an `ffi_bridge` `Call`,
    converting the raw `PyObject*` result back into a real Python object exactly like
    `call_python_object` already does for code it built itself. This is what actually lets a
    real, PARSED `.tamil` source program drive a real Tier-0 kernel capability (T9.1's own
    honest-scope gap, closed here) -- not a hand-passed Python reference, a genuinely compiled
    statement.

    `frame_bytes` (G8): 0 (the default) is the ORIGINAL T8.11/T9.2 shape -- a single bare
    `ffi_bridge` call, the goal's entire body, no locals, byte-identical to `emit_python_call`
    alone -- uses the bare single-`sub rsp` unwind path (`run_python_call_raw`), UNCHANGED. Any
    other value is a REAL frame size `compile_goal`'s own `_out_frame_size` out-param reports
    (G8 lifted the one-bare-call restriction) -- uses `run_framed_call_with_unwind` instead, the
    isolated-and-verified unwind-info extension for a real `push rbp` frame."""
    if frame_bytes:
        return _convert_result_ptr(
            run_framed_call_with_unwind(code, frame_bytes), "the compiled .tamil capability-call"
        )
    return _run_and_convert(code, "the compiled .tamil capability-call")


__all__ = [
    "PythonCallFailed",
    "call_python_object",
    "call_python_object_vectorcall",
    "call_python_object_with_args",
    "python_call_object_address",
    "run_compiled_capability_call",
]
