"""Memory-sovereignty — the deterministic conformance suite (C5, framework-10x Part C).

Mirrors C1-C4 exactly: the 3 s33 user-sovereign-memory capabilities (`FileMemoryStore`/
`dump_memory`/`parse_memory` / `parse_quick_adds`+`quick_add` / `export_memory`+`verify_bundle`)
are pure file/hash mechanics — a frontmatter roundtrip, a content-hashed capture, a tamper-evident
bundle are properties of the code, not agent decisions. Every case is a direct call into the real
module (real temp files for the filesystem-backed ones). Zero LLM tokens spent.

Composes the existing engine (same `Scenario`-shaped JSON + partition convention + `Suite.run()`
external-suite dispatch point) exactly like C1-C4 — no engine change.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

from madras.eval_.proving_ground.suite import Case, Suite
from madras.memory.file_memory import FileMemoryStore, dump_memory, import_from_files, parse_memory
from madras.memory.portability import export_memory, verify_bundle
from madras.memory.quick_add import capture_quick_adds, parse_quick_adds, quick_add
from madras.memory.retrieval import MemoryItem

DATA_DIR = Path(__file__).resolve().parent / "memory_sovereignty" / "data"
_FEATURES = ["file_memory_frontmatter", "file_memory_quick_add", "memory_import_portability"]


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


def _item(**overrides: Any) -> MemoryItem:
    base: dict[str, Any] = dict(
        id="mem-1",
        kind="note",
        subject="acme",
        content="deploys on fridays",
        tags=["ops"],
        confidence=0.9,
        source="test",
        created_at=100.0,
        valid_from=100.0,
    )
    base.update(overrides)
    return MemoryItem(**base)


def _run_async(coro: Any) -> Any:
    """Run `coro` regardless of whether an event loop is already running. `asyncio.run()` alone
    raises when called from inside a running loop — the exact situation when this suite is driven
    by an async caller (e.g. the FastAPI `/proving-ground/moat` route, found live via the C6
    endpoint test, not assumed). If a loop is already running, execute the coroutine on its own
    loop in a separate thread instead of nesting."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Per-module executors — each runs the REAL memory-sovereignty code against
# the case's adversarial (or happy-path) setup and returns (passed, detail).
# ---------------------------------------------------------------------------


class _FakeFabric:
    """A hermetic stand-in for MemoryFabric — records whether .remember() was actually called
    (proving import goes through arbitration) vs a raw insert."""

    def __init__(self) -> None:
        self.remember_calls: list[MemoryItem] = []

    async def remember(self, item: MemoryItem, *, now: float) -> list[str]:
        del now
        self.remember_calls.append(item)
        return [item.id]


def _exec_file_memory(attack: str) -> tuple[bool, str]:
    if attack == "dump_parse_roundtrip":
        item = _item()
        parsed = parse_memory(dump_memory(item))
        ok = parsed == item
        return ok, f"parsed={parsed}"

    if attack == "store_write_read":
        with tempfile.TemporaryDirectory() as root:
            store = FileMemoryStore(root=root)
            item = _item()
            store.write(item)
            read_back = store.read(item.id)
            ok = read_back == item
            return ok, f"read_back={read_back}"

    if attack == "delete_missing":
        with tempfile.TemporaryDirectory() as root:
            store = FileMemoryStore(root=root)
            result = store.delete("never-written")
            return result is False, f"delete_result={result}"

    if attack == "tenant_isolation":
        with tempfile.TemporaryDirectory() as root:
            store_a = FileMemoryStore(root=root, agent_name="shadow", tenant="acme")
            store_b = FileMemoryStore(root=root, agent_name="shadow", tenant="other-corp")
            store_a.write(_item(id="m1", content="acme's secret"))
            store_b.write(_item(id="m1", content="other-corp's secret"))
            a_read = store_a.read("m1")
            b_read = store_b.read("m1")
            assert a_read is not None and b_read is not None, "just-written items must read back"
            ok = a_read.content == "acme's secret" and b_read.content == "other-corp's secret"
            return ok, f"a={a_read.content!r} b={b_read.content!r}"

    if attack == "import_reconciles_through_remember":
        with tempfile.TemporaryDirectory() as root:
            store = FileMemoryStore(root=root)
            store.write(_item())
            fabric = _FakeFabric()
            _run_async(import_from_files(fabric, store, now=200.0))
            ok = len(fabric.remember_calls) == 1 and fabric.remember_calls[0].id == "mem-1"
            return ok, f"remember_calls={len(fabric.remember_calls)}"

    return False, f"unknown attack {attack!r}"


def _exec_quick_add(attack: str) -> tuple[bool, str]:
    if attack == "parses_remember":
        captures = parse_quick_adds("#remember buy milk")
        ok = len(captures) == 1 and captures[0].content == "buy milk"
        return ok, f"captures={captures}"

    if attack == "parses_subject_colon":
        captures = parse_quick_adds("#mem acme: deploys fridays")
        ok = (
            len(captures) == 1
            and captures[0].subject == "acme"
            and captures[0].content == "deploys fridays"
        )
        return ok, f"captures={captures}"

    if attack == "not_markdown_heading":
        captures = parse_quick_adds("# Heading\nsome body text")
        return len(captures) == 0, f"captures={captures}"

    if attack == "idempotent_same_content":
        with tempfile.TemporaryDirectory() as root:
            store = FileMemoryStore(root=root)
            first = quick_add("buy milk", store=store, now=100.0)
            second = quick_add("buy milk", store=store, now=200.0)  # same content, later time
            all_items = store.list()
            ok = first.id == second.id and len(all_items) == 1
            return ok, f"first.id={first.id} second.id={second.id} count={len(all_items)}"

    if attack == "different_content_different_id":
        with tempfile.TemporaryDirectory() as root:
            store = FileMemoryStore(root=root)
            captured = capture_quick_adds("#remember buy milk\n#remember buy eggs", store=store)
            ids = {i.id for i in captured}
            ok = len(ids) == 2
            return ok, f"ids={ids}"

    return False, f"unknown attack {attack!r}"


def _exec_portability(attack: str) -> tuple[bool, str]:
    items = [_item(id="m1", content="fact one"), _item(id="m2", content="fact two")]

    if attack == "export_verifiable":
        bundle = export_memory(items)
        ok = verify_bundle(bundle) is True
        return ok, f"verify={verify_bundle(bundle)}"

    if attack == "tampered_fails":
        bundle = export_memory(items)
        bundle["items"][0]["item"]["content"] = "TAMPERED"  # mutate after export
        ok = verify_bundle(bundle) is False
        return ok, f"verify_after_tamper={verify_bundle(bundle)}"

    if attack == "unsigned_still_verifiable":
        bundle = export_memory(items)
        ok = "signature" not in bundle and verify_bundle(bundle) is True
        return ok, f"has_signature={'signature' in bundle} verify={verify_bundle(bundle)}"

    if attack == "root_order_independent":
        bundle_a = export_memory(items)
        bundle_b = export_memory(list(reversed(items)))
        ok = bundle_a["root"] == bundle_b["root"]
        return ok, f"root_a={bundle_a['root']} root_b={bundle_b['root']}"

    if attack == "signed_bundle_carries_signature":
        bundle = export_memory(items, sign=lambda root: f"sig-of-{root[:8]}")
        ok = "signature" in bundle and bundle["signature"] == f"sig-of-{bundle['root'][:8]}"
        return ok, f"signature={bundle.get('signature')}"

    return False, f"unknown attack {attack!r}"


_EXECUTORS: dict[str, Callable[[dict[str, Any]], tuple[bool, str]]] = {
    "file_memory": lambda s: _exec_file_memory(s["attack"]),
    "quick_add": lambda s: _exec_quick_add(s["attack"]),
    "portability": lambda s: _exec_portability(s["attack"]),
}


def run_case(row: dict[str, Any]) -> dict[str, Any]:
    """Execute one adversarial/happy-path case against the real memory-sovereignty module."""
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
        "suite_id": "memory_sovereignty_conformance",
        "benchmark_family": "memory_sovereignty_conformance",
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


class MemorySovereigntyConformanceSuite(Suite):
    id: str = "memory_sovereignty_conformance"
    name: str = "Memory-sovereignty — deterministic conformance"
    version: str = "v1"
    kind: Literal["external", "native", "dataset"] = "external"
    provenance: str = (
        "Madras-original, deterministic (zero LLM) — direct adversarial + "
        "happy-path calls into FileMemoryStore/dump_memory/parse_memory, "
        "parse_quick_adds/quick_add, export_memory/verify_bundle. "
        "Public + held_out partitions."
    )
    features: list[str] = Field(default_factory=lambda: list(_FEATURES))
    partition: str | None = None  # None = both partitions (the official Index view)

    def load_cases(self) -> list[Case]:
        """Lightweight coverage-stub Cases (one per module), matching the convention every other
        external suite (tau2/identity_boundary/routing_resilience/durable_state/
        compile_conformance/...) follows — this suite self-drives via `run()`."""
        return [
            Case(
                id=f"memory_sovereignty-{module}",
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
