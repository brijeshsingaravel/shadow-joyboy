"""kollan_modules.py -- G7 (plan-local D69): multi-file `.tamil` programs, the closed-tree half
of `import`. Resolution is entirely a pre-codegen, closed-tree concern (D58's own "declaring is
open, resolving/executing is closed" split) -- `tamil_lang`'s parser only ever sees ONE file at a
time; this module is where a real program spanning multiple files becomes the single flat
`fndefs: dict[str, FnDef]` `madras.dsl.kollan.run_compiled_fndefs` already knows how to compile
and run (G1, unchanged -- G7 needs ZERO new codegen, only orchestration).

Research (GPL-LLM-OSS Radar, s56): Zig's `@import` for the "no separate module-namespace type"
simplicity (each file is implicitly a flat namespace of its own top-level `fn` defs, resolution
is 100% static/hermetic) + Go's whole-program import-DAG discipline for cycle-safe multi-file
resolution -- but unlike Go, circular imports here are SUPPORTED (founder-chosen scope, s56):
`run_compiled_fndefs`'s own two-pass placeholder-then-patch scheme was already built general
enough for arbitrary (including mutually recursive) call graphs within one file's `fndefs` dict;
extending that dict to span multiple, even cyclically-importing, files works transparently, since
`run_compiled_fndefs` has no notion of "file" at all -- only fn names.

Every fn gets a GLOBALLY unique key (its resolved file path + its own name) regardless of which
file it lives in or what alias other files import it under -- this is what makes cross-file same-
name fns (in DIFFERENT files) collision-free without needing a real symbol-visibility system.
Re-using the same import alias twice in ONE file shadows the earlier binding (last-imported-wins,
a deliberate, disclosed v0 simplification over a collision error -- founder-chosen, s56).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from tamil_lang.ast import Bind, Branch, Call, FnDef, Goal, Import, Loop, Statement
from tamil_lang.kollan import UnsupportedNode
from tamil_lang.kural import parse_program

_Node = Goal | FnDef | Import
_Resolver = Callable[[str | None, str], str]


def _rewrite_call(call: Call, resolve: _Resolver) -> Call:
    if "." in call.name:
        alias, fn_name = call.name.split(".", 1)
        target = resolve(alias, fn_name)
    else:
        target = resolve(None, call.name)
    return call.model_copy(update={"name": target})


def _rewrite_stmts(stmts: list[Statement], resolve: _Resolver) -> list[Statement]:
    out: list[Statement] = []
    for stmt in stmts:
        if isinstance(stmt, Call):
            out.append(_rewrite_call(stmt, resolve))
        elif isinstance(stmt, Bind) and isinstance(stmt.call, Call):
            out.append(stmt.model_copy(update={"call": _rewrite_call(stmt.call, resolve)}))
        elif isinstance(stmt, Branch):
            out.append(
                stmt.model_copy(
                    update={
                        "then": _rewrite_stmts(stmt.then, resolve),
                        "otherwise": _rewrite_stmts(stmt.otherwise, resolve),
                    }
                )
            )
        elif isinstance(stmt, Loop):
            out.append(stmt.model_copy(update={"body": _rewrite_stmts(stmt.body, resolve)}))
        else:
            out.append(stmt)
    return out


def resolve_program(entry_path: str | Path, entry_fn: str) -> tuple[dict[str, FnDef], str]:
    """Parse `entry_path` and every `import`-reachable `.tamil` file (transitively, cycle-safe --
    a file already discovered is never re-parsed), merge every file's `fn` defs into ONE flat,
    globally-keyed `fndefs` dict, and return `(fndefs, entry_key)` ready for
    `madras.dsl.kollan.run_compiled_fndefs(fndefs, entry_name=entry_key)`. `Goal`s in an imported
    file are ignored (v0 scope: only `fn`s are importable, matching Zig's own "each file is a
    namespace of its top-level decls" simplicity)."""
    entry_path = Path(entry_path).resolve()
    file_nodes: dict[Path, list[_Node]] = {}
    file_aliases: dict[Path, dict[str, Path]] = {}

    def discover(path: Path) -> None:
        if path in file_nodes:
            return  # already discovered -- cycle-safe: a file is parsed at most once
        file_nodes[path] = []  # placeholder, breaks infinite recursion on a real import cycle
        nodes = parse_program(path.read_text(encoding="utf-8"))
        aliases: dict[str, Path] = {}
        for node in nodes:
            if isinstance(node, Import):
                target = (path.parent / node.path).resolve()
                aliases[node.alias] = target  # last-imported-wins shadowing (founder-chosen)
                discover(target)
        file_nodes[path] = nodes
        file_aliases[path] = aliases

    discover(entry_path)

    def global_key(path: Path, fn_name: str) -> str:
        return f"{path}::{fn_name}"

    known_fns = {
        (path, node.name)
        for path, nodes in file_nodes.items()
        for node in nodes
        if isinstance(node, FnDef)
    }

    fndefs: dict[str, FnDef] = {}
    for path, nodes in file_nodes.items():
        aliases = file_aliases[path]

        def _resolve(
            alias: str | None,
            fn_name: str,
            path: Path = path,
            aliases: dict[str, Path] = aliases,
        ) -> str:
            if alias is None:
                if (path, fn_name) not in known_fns:
                    # Not a same-file `fn` -- an ordinary external capability call (e.g. `log`,
                    # an FFI bridge, another user fn's own recursive self-call handled the same
                    # way G1 always has). Leave the bare name UNCHANGED; `capability_addresses`
                    # resolves it at compile time exactly like every pre-G7 program already did.
                    return fn_name
                return global_key(path, fn_name)
            if alias not in aliases:
                raise UnsupportedNode(
                    f"{path}: call references unknown import alias {alias!r} -- known "
                    f"imports: {sorted(aliases)}"
                )
            target_path = aliases[alias]
            if (target_path, fn_name) not in known_fns:
                raise UnsupportedNode(
                    f"{path}: call references fn {fn_name!r} in {target_path}, which doesn't "
                    "define it"
                )
            return global_key(target_path, fn_name)

        for node in nodes:
            if isinstance(node, FnDef):
                rewritten_body = _rewrite_stmts(node.body, _resolve)
                fndefs[global_key(path, node.name)] = node.model_copy(
                    update={"body": rewritten_body}
                )
            elif isinstance(node, Goal):
                pass  # v0 scope: only fn defs are importable/mergeable across files

    if (entry_path, entry_fn) not in known_fns:
        raise UnsupportedNode(
            f"{entry_path} doesn't define fn {entry_fn!r} -- known fns in that file: "
            f"{sorted(n.name for n in file_nodes[entry_path] if isinstance(n, FnDef))}"
        )
    return fndefs, global_key(entry_path, entry_fn)


__all__ = ["resolve_program"]
