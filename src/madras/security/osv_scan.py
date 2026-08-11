"""OSV dependency-vulnerability scan (ASI04 supply-chain) — query the free OSV.dev database
for known vulns in a project's pinned dependencies.

The network query is injectable, so the policy is unit-testable offline and the live path makes
exactly ONE batched call (no per-package hammering). Reports advisory ids per (package, version).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
_REQ_LINE = re.compile(r"^([A-Za-z0-9._-]+)\s*==\s*([^\s;]+)")


@dataclass
class Dependency:
    name: str
    version: str
    ecosystem: str = "PyPI"


@dataclass
class OsvFinding:
    package: str
    version: str
    vuln_id: str
    ecosystem: str = "PyPI"


def parse_requirements(text: str) -> list[Dependency]:
    """Parse pinned (`name==version`) deps from a requirements.txt-style text."""
    deps: list[Dependency] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _REQ_LINE.match(line)
        if m:
            deps.append(Dependency(m.group(1), m.group(2)))
    return deps


async def query_osv(deps: list[Dependency], *, timeout: float = 20.0) -> list[list[str]]:
    """Batch-query OSV.dev; return the list of vuln ids per dependency (same order)."""
    payload = {
        "queries": [
            {"package": {"name": d.name, "ecosystem": d.ecosystem}, "version": d.version}
            for d in deps
        ]
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(OSV_BATCH_URL, json=payload)
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        results: list[dict[str, Any]] = data.get("results", [])
    return [[v.get("id", "") for v in (res or {}).get("vulns", [])] for res in results]


async def scan_dependencies(
    deps: list[Dependency],
    *,
    query: Callable[[list[Dependency]], Awaitable[list[list[str]]]] = query_osv,
) -> list[OsvFinding]:
    """Scan deps for known OSV vulns. `query` is injectable for offline testing."""
    if not deps:
        return []
    per_dep = await query(deps)
    findings: list[OsvFinding] = []
    for dep, ids in zip(deps, per_dep, strict=False):
        for vid in ids:
            if vid:
                findings.append(OsvFinding(dep.name, dep.version, vid, dep.ecosystem))
    return findings
