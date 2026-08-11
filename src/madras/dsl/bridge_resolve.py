"""Phase U -- the universal bridge's in_process dispatcher: takes a real `BridgeManifest`
(transport=in_process) and actually calls it, generalizing G8's/N5's hand-wired
`capability_addresses` pattern into a single manifest-driven entrypoint instead of a test file
building its own `capability_addresses` dict by hand each time.

Two real, distinct resolution+call paths, matching the two real cases the schema (D79) already
proves out -- NOT unified into one mechanism, because they genuinely aren't the same shape:

  DllExportResolve      -- generalizes N5: resolve a real address out of a real `.dll` (via
                           `ctypes.CDLL`), then call it AS NATIVE CODE by synthesizing the
                           smallest possible real `.tamil` goal (`compile_goal` + G8's own
                           `capability_addresses` mechanism, unchanged).
  PythonCallableResolve -- generalizes G8: resolve a real, already-loaded Python callable (via
                           `importlib`), then call it through `kollan_bridge`'s own
                           `PyObject_CallObject` bridge (`call_python_object_with_args`),
                           unchanged.

v0 boundary (same one G8/N5 already disclosed): args are raw integers only, matching every
other arg-passing boundary already established in this codebase.
"""

from __future__ import annotations

import ctypes
import importlib
from collections.abc import Callable

from tamil_lang import Bind, Call, Goal, Remember, compile_goal
from tamil_lang.ast import Statement

from madras.dsl.kollan import run_compiled_goal
from madras.dsl.kollan_bridge import call_python_object_with_args
from madras.models.bridge_manifest import (
    BridgeManifest,
    DllExportResolve,
    PythonCallableResolve,
    Transport,
)


class BridgeResolutionError(RuntimeError):
    """A manifest's `resolve` couldn't actually be turned into a real address/callable."""


def _resolve_dll_export(resolve: DllExportResolve) -> int:
    try:
        lib = ctypes.CDLL(resolve.dll_path)
    except OSError as exc:
        raise BridgeResolutionError(f"could not load {resolve.dll_path!r}") from exc
    try:
        fn = getattr(lib, resolve.export_name)
    except AttributeError as exc:
        raise BridgeResolutionError(
            f"{resolve.export_name!r} is not an export of {resolve.dll_path!r}"
        ) from exc
    addr = ctypes.cast(fn, ctypes.c_void_p).value
    if addr is None:
        raise BridgeResolutionError(
            f"could not resolve {resolve.export_name!r} in {resolve.dll_path!r}"
        )
    return addr


def _resolve_python_callable(resolve: PythonCallableResolve) -> Callable[..., object]:
    module = importlib.import_module(resolve.module)
    obj: object = module
    for part in resolve.qualname.split("."):
        obj = getattr(obj, part)
    if not callable(obj):
        raise BridgeResolutionError(
            f"{resolve.module}.{resolve.qualname} resolved to a non-callable {obj!r}"
        )
    return obj


def _build_single_call_goal(capability_name: str, args: tuple[int, ...]) -> Goal:
    """The smallest real `.tamil` goal that calls exactly one named capability with `args`
    (integer values) -- built here (not hand-typed per call site) since the manifest is the one
    thing that varies, not the shape of the call itself. `compile_goal` has no `Return` node for
    a top-level goal (that's `compile_fndef`'s concern); its own convention (matching N5's own
    `llama_ffi.tamil`) is that the FINAL `Bind`'s call result is what the compiled function
    returns, so the capability call is deliberately the last statement."""
    arg_names = [f"__bridge_arg{i}" for i in range(len(args))]
    body: list[Statement] = [
        Remember(key=name, value=str(value)) for name, value in zip(arg_names, args, strict=True)
    ]
    call = Call(name=capability_name, args=list(arg_names))
    body.append(Bind(target="__bridge_result", call=call))
    return Goal(intent=f"bridge dispatch: {capability_name}", body=body)


def dispatch_in_process(manifest: BridgeManifest, *args: int) -> object:
    """Resolve `manifest.in_process_interface.resolve` to a real address/callable and actually
    call it with `args`, returning whatever the real call actually returned."""
    if manifest.transport is not Transport.IN_PROCESS:
        raise ValueError(
            f"dispatch_in_process only handles transport=in_process, got {manifest.transport!r}"
        )
    iface = manifest.in_process_interface
    assert iface is not None  # enforced by BridgeManifest's own transport/interface validator
    resolve = iface.resolve

    if isinstance(resolve, PythonCallableResolve):
        callable_obj = _resolve_python_callable(resolve)
        return call_python_object_with_args(callable_obj, *args)

    addr = _resolve_dll_export(resolve)
    goal = _build_single_call_goal(manifest.name, args)
    code = compile_goal(goal, "x86_64", {manifest.name: addr})
    return run_compiled_goal(code)


__all__ = ["BridgeResolutionError", "dispatch_in_process"]
