"""Compile-conformance — the deterministic conformance suite (C4, framework-10x Part C).

Mirrors C1/C2/C3 exactly: the 5 s33 compile-pipeline capabilities (`merge_scopes` /
`merge_with_provenance` / `discover_context_files`+`resolve_imports` / `FileReloader` /
`parse_agent_markdown`) are pure config/file mechanics — precedence, provenance, tree-walk,
content-hash reload, and markdown parsing are all deterministic transforms, not agent decisions.
Every case is a direct call into the real module (with real temp files for the filesystem-backed
ones). Zero LLM tokens spent.

Composes the existing engine (same `Scenario`-shaped JSON + partition convention + `Suite.run()`
external-suite dispatch point) exactly like C1/C2/C3 — no engine change.
"""

from __future__ import annotations

import json
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.agent_markdown import parse_agent_markdown
from madras.eval_.proving_ground.suite import Case, Suite
from madras.factory.agent_context import discover_context_files, resolve_imports
from madras.factory.hot_reload import FileReloader
from madras.factory.merge_provenance import explain, merge_with_provenance, overridden_keys
from madras.factory.scope_config import ScopeLayer, merge_scopes
from madras.security.permissions import Decision

DATA_DIR = Path(__file__).resolve().parent / "compile_conformance" / "data"
_FEATURES = [
    "scope_layer_precedence",
    "merge_provenance",
    "repo_context_discovery",
    "hot_reload_on_edit",
    "agent_as_markdown",
]


def _load_cases(partition: str | None) -> list[dict[str, Any]]:
    files = {
        "public": [DATA_DIR / "public.json"],
        "held_out": [DATA_DIR / "held_out.json"],
        None: [DATA_DIR / "public.json", DATA_DIR / "held_out.json"],
    }[partition]
    rows: list[dict[str, Any]] = []
    for f in files:
        if f.exists():
            rows.extend(json.loads(f.read_text(encoding="utf-8")))
    return rows


# ---------------------------------------------------------------------------
# Per-module executors — each runs the REAL compile-pipeline code against
# the case's adversarial (or happy-path) setup and returns (passed, detail).
# ---------------------------------------------------------------------------


def _exec_scope_config(attack: str) -> tuple[bool, str]:
    if attack == "later_overrides_earlier":
        layers = [
            ScopeLayer("platform", {"persona": "platform-default"}),
            ScopeLayer("seller", {"persona": "seller-template"}),
            ScopeLayer("org", {}),
            ScopeLayer("user", {"persona": "user-choice"}),
        ]
        result = merge_scopes(layers)
        ok = result.config.get("persona") == "user-choice"
        return ok, f"persona={result.config.get('persona')}"

    if attack == "lock_blocks_override":
        layers = [
            ScopeLayer("platform", {"safety.floor": "strict"}, locked=["safety.floor"]),
            ScopeLayer("seller", {"safety.floor": "loose"}),
            ScopeLayer("org", {}),
            ScopeLayer("user", {"safety.floor": "off"}),
        ]
        result = merge_scopes(layers)
        ok = (
            result.config.get("safety", {}).get("floor") == "strict"
            and "seller:safety.floor" in result.rejected
            and "user:safety.floor" in result.rejected
        )
        return ok, f"floor={result.config.get('safety')} rejected={result.rejected}"

    if attack == "unlocked_flows_normally":
        layers = [
            ScopeLayer("platform", {"theme": "default"}),
            ScopeLayer("seller", {}),
            ScopeLayer("org", {}),
            ScopeLayer("user", {"theme": "dark"}),
        ]
        result = merge_scopes(layers)
        ok = result.config.get("theme") == "dark" and not result.rejected
        return ok, f"theme={result.config.get('theme')} rejected={result.rejected}"

    if attack == "lock_binds_only_lower":
        layers = [
            ScopeLayer("platform", {"safety.floor": "strict"}, locked=["safety.floor"]),
            ScopeLayer("seller", {}),
            ScopeLayer("org", {}),
            ScopeLayer("user", {}),
        ]
        result = merge_scopes(layers)
        ok = result.config.get("safety", {}).get("floor") == "strict" and not result.rejected
        return ok, f"floor={result.config.get('safety')} rejected={result.rejected}"

    return False, f"unknown attack {attack!r}"


def _exec_merge_provenance(attack: str) -> tuple[bool, str]:
    layers: list[tuple[str, dict[str, Any]]] = [
        ("platform", {"persona": "platform-default", "rank": "intern"}),
        ("seller", {"persona": "seller-persona"}),
        ("user", {}),
    ]

    if attack == "origin_final_layer":
        prov = merge_with_provenance(layers)
        ok = prov.origin.get("persona") == "seller"
        return ok, f"origin={prov.origin.get('persona')}"

    if attack == "history_full_chain":
        prov = merge_with_provenance(layers)
        ok = prov.history.get("persona") == ["platform", "seller"]
        return ok, f"history={prov.history.get('persona')}"

    if attack == "overridden_excludes_single_layer":
        prov = merge_with_provenance(layers)
        overridden = overridden_keys(prov)
        ok = "rank" not in overridden and "persona" in overridden
        return ok, f"overridden_keys={list(overridden)}"

    if attack == "explain_readable_chain":
        prov = merge_with_provenance(layers)
        text = explain(prov, "persona")
        ok = "seller" in text and "platform" in text
        return ok, f"explain={text!r}"

    return False, f"unknown attack {attack!r}"


def _exec_agent_context(attack: str) -> tuple[bool, str]:
    if attack == "discover_nearest_wins_order":
        with tempfile.TemporaryDirectory() as root:
            root_p = Path(root)
            (root_p / "AGENTS.md").write_text("root rules", encoding="utf-8")
            sub = root_p / "pkg"
            sub.mkdir()
            (sub / "AGENTS.md").write_text("pkg rules", encoding="utf-8")
            files = discover_context_files(sub, stop_at=root_p)
            ok = len(files) == 2 and files[0].parent == root_p and files[1].parent == sub
            return ok, f"order={[str(f) for f in files]}"

    if attack == "discover_stops_at_boundary":
        with tempfile.TemporaryDirectory() as root:
            root_p = Path(root)
            (root_p / "AGENTS.md").write_text("above boundary", encoding="utf-8")
            boundary = root_p / "repo"
            boundary.mkdir()
            (boundary / "AGENTS.md").write_text("in repo", encoding="utf-8")
            files = discover_context_files(boundary, stop_at=boundary)
            ok = len(files) == 1 and files[0].parent == boundary
            return ok, f"found={[str(f) for f in files]}"

    if attack == "import_expands":
        with tempfile.TemporaryDirectory() as root:
            root_p = Path(root)
            (root_p / "shared.md").write_text("SHARED CONTENT", encoding="utf-8")
            result = resolve_imports("intro\n@shared.md\noutro", root_p, root=root_p)
            ok = "SHARED CONTENT" in result.text and "shared.md" in result.imported
            return ok, f"text={result.text!r} imported={result.imported}"

    if attack == "import_outside_repo":
        with tempfile.TemporaryDirectory() as outer:
            outer_p = Path(outer)
            (outer_p / "secret.md").write_text("OUTSIDE SECRET", encoding="utf-8")
            repo = outer_p / "repo"
            repo.mkdir()
            result = resolve_imports("@../secret.md", repo, root=repo)
            ok = "OUTSIDE SECRET" not in result.text and any("outside" in s for s in result.skipped)
            return ok, f"text={result.text!r} skipped={result.skipped}"

    if attack == "import_cycle":
        with tempfile.TemporaryDirectory() as root:
            root_p = Path(root)
            (root_p / "a.md").write_text("A\n@b.md", encoding="utf-8")
            (root_p / "b.md").write_text("B\n@a.md", encoding="utf-8")
            t0 = time.perf_counter()
            result = resolve_imports(
                (root_p / "a.md").read_text(encoding="utf-8"), root_p, root=root_p
            )
            elapsed = time.perf_counter() - t0
            ok = elapsed < 5.0 and any("cycle" in s for s in result.skipped)
            return ok, f"elapsed={elapsed:.3f}s skipped={result.skipped}"

    if attack == "import_max_depth":
        with tempfile.TemporaryDirectory() as root:
            root_p = Path(root)
            (root_p / "d0.md").write_text("@d1.md", encoding="utf-8")
            (root_p / "d1.md").write_text("@d2.md", encoding="utf-8")
            (root_p / "d2.md").write_text("@d3.md", encoding="utf-8")
            (root_p / "d3.md").write_text("DEEP", encoding="utf-8")
            result = resolve_imports("@d0.md", root_p, root=root_p, max_depth=1)
            ok = "DEEP" not in result.text and any("max depth" in s for s in result.skipped)
            return ok, f"text={result.text!r} skipped={result.skipped}"

    return False, f"unknown attack {attack!r}"


def _exec_hot_reload(attack: str) -> tuple[bool, str]:
    if attack == "register_loads_once":
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "agent.md")
            Path(path).write_text("v1", encoding="utf-8")
            reloader = FileReloader()
            event = reloader.register(path, lambda p: Path(p).read_text(encoding="utf-8"))
            ok = event.kind == "loaded" and event.value == "v1"
            return ok, f"kind={event.kind} value={event.value!r}"

    if attack == "detects_content_change":
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "agent.md")
            Path(path).write_text("v1", encoding="utf-8")
            reloader = FileReloader()
            reloader.register(path, lambda p: Path(p).read_text(encoding="utf-8"))
            Path(path).write_text("v2", encoding="utf-8")
            events = reloader.poll()
            ok = len(events) == 1 and events[0].kind == "changed" and events[0].value == "v2"
            return ok, f"events={[(e.kind, e.value) for e in events]}"

    if attack == "ignores_noop_rewrite":
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "agent.md")
            Path(path).write_text("same content", encoding="utf-8")
            reloader = FileReloader()
            reloader.register(path, lambda p: Path(p).read_text(encoding="utf-8"))
            Path(path).write_text("same content", encoding="utf-8")  # byte-identical rewrite
            events = reloader.poll()
            return not events, f"events={events}"

    if attack == "detects_deletion":
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / "agent.md")
            Path(path).write_text("v1", encoding="utf-8")
            reloader = FileReloader()
            reloader.register(path, lambda p: Path(p).read_text(encoding="utf-8"))
            Path(path).unlink()
            events = reloader.poll()
            ok = len(events) == 1 and events[0].kind == "deleted" and reloader.current(path) is None
            return ok, f"events={[(e.kind, e.value) for e in events]}"

    return False, f"unknown attack {attack!r}"


def _exec_agent_markdown(attack: str) -> tuple[bool, str]:
    if attack == "parses_frontmatter_and_body":
        doc = parse_agent_markdown(
            "---\nname: my-agent\nmodel: llama-70b\n---\nYou are a helpful assistant."
        )
        ok = (
            doc.name == "my-agent"
            and doc.fields.get("model") == "llama-70b"
            and doc.instructions == "You are a helpful assistant."
        )
        return ok, f"name={doc.name} fields={doc.fields} instructions={doc.instructions!r}"

    if attack == "permissions_simple":
        doc = parse_agent_markdown("---\nname: a\npermissions:\n  file_write: allow\n---\nbody")
        ok = (
            len(doc.permission_rules) == 1
            and doc.permission_rules[0].tool == "file_write"
            and doc.permission_rules[0].decision == Decision.ALLOW
            and doc.permission_rules[0].arg_pattern == "*"
        )
        return ok, f"rules={doc.permission_rules}"

    if attack == "permissions_scoped":
        doc = parse_agent_markdown(
            "---\nname: a\npermissions:\n  terminal:\n    'rm *': deny\n---\nbody"
        )
        ok = (
            len(doc.permission_rules) == 1
            and doc.permission_rules[0].tool == "terminal"
            and doc.permission_rules[0].arg_pattern == "rm *"
            and doc.permission_rules[0].decision == Decision.DENY
        )
        return ok, f"rules={doc.permission_rules}"

    if attack == "no_frontmatter":
        plain = "just plain instructions, no frontmatter block"
        doc = parse_agent_markdown(plain)
        ok = doc.fields == {} and doc.instructions == plain
        return ok, f"fields={doc.fields} instructions={doc.instructions!r}"

    return False, f"unknown attack {attack!r}"


_EXECUTORS: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "scope_config": lambda s: _exec_scope_config(s["attack"]),
    "merge_provenance": lambda s: _exec_merge_provenance(s["attack"]),
    "agent_context": lambda s: _exec_agent_context(s["attack"]),
    "hot_reload": lambda s: _exec_hot_reload(s["attack"]),
    "agent_markdown": lambda s: _exec_agent_markdown(s["attack"]),
}


def run_case(row: dict[str, Any]) -> dict[str, Any]:
    """Execute one adversarial/happy-path case against the real compile-pipeline module."""
    setup = row["setup"]
    executor = _EXECUTORS[setup["module"]]
    t0 = time.perf_counter()
    try:
        passed, detail = executor(setup)
    except Exception as exc:  # a raising module is a conformance FAILURE, not a crash
        passed, detail = False, f"executor raised: {exc!r}"
    latency_ms = (time.perf_counter() - t0) * 1000.0
    return {
        "scenario_id": row["id"],
        "suite_id": "compile_conformance",
        "benchmark_family": "compile_conformance",
        "features": row.get("features", []),
        "k": 1,
        "passes": 1 if passed else 0,
        "pass_rate": 1.0 if passed else 0.0,
        "det": [{"type": "security_verdict", "passed": passed, "detail": detail}],
        "judge_pass": None,
        "verdict": "pass" if passed else "fail",
        "n_steps": 1,
        "tool_error_rate": 0.0,
        "latency_ms": round(latency_ms, 3),
        "tokens": 0,
    }


class CompileConformanceSuite(Suite):
    id: str = "compile_conformance"
    name: str = "Compile-conformance — deterministic conformance"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct adversarial + "
        "happy-path calls into merge_scopes/merge_with_provenance/"
        "discover_context_files+resolve_imports/FileReloader/parse_agent_markdown. "
        "Public + held_out partitions."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    partition: str | None = None  # None = both partitions (the official Index view)

    def load_cases(self) -> list[Case]:
        """Lightweight coverage-stub Cases (one per module), matching the convention every other
        external suite (tau2/identity_boundary/routing_resilience/durable_state/...) follows —
        this suite self-drives via `run()`, so these are not executed through the governed runner.
        """
        return [
            Case(
                id=f"compile_conformance-{module}",
                suite_id=self.id,
                benchmark_family=self.id,
                features=[module],
                tools=[],
                prompt=f"{self.name}: {module} conformance cases (external; zero-LLM)",
            )
            for module in sorted(_EXECUTORS)
        ]

    def run(self, model: str, k: int, concurrency: int) -> list[dict[str, Any]]:
        del model, k, concurrency  # deterministic + zero-cost: irrelevant, no LLM call at all
        return [run_case(row) for row in _load_cases(self.partition)]
