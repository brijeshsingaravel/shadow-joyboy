"""MCP security — tool-poisoning scan + manifest pinning (rug-pull defense). ASI04.

The MCP attack surface the field hasn't solved: a tool's DESCRIPTION/SCHEMA (which the user
never sees) carries hidden instructions that execute with host privileges (tool poisoning,
CVE-2025-54136 = OWASP ASI01), and a server approved once silently mutates later (rug-pull,
never re-verified). Defenses, both pure + deterministic:
  * scan_tool: flag injection/poisoning patterns in name/description/schema before trust.
  * manifest_hash + verify_pin: pin a server's tool manifest; re-verify on every reconnect
    and quarantine on drift.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

# Instruction-like / exfiltration patterns that have NO business in a tool description.
_POISON = [
    (
        re.compile(r"\bignore (all |the |any )?(previous|prior|above|earlier)\b", re.I),
        "tells the agent to ignore prior instructions",
    ),
    (
        re.compile(
            r"\b(disregard|override|bypass)\b.{0,40}\b(instruction|rule|policy|guard)", re.I
        ),
        "tells the agent to override its rules",
    ),
    (
        re.compile(r"<\s*(important|system|secret|hidden|instructions?)\s*>", re.I),
        "embeds a hidden system/important block",
    ),
    (
        re.compile(r"\bdo not (tell|inform|mention|reveal)\b", re.I),
        "asks the agent to hide something from the user",
    ),
    (
        re.compile(
            r"\b(exfiltrat|send (the )?(secret|token|key|password|credential)|"
            r"\.ssh|/etc/passwd|env(ironment)? variable)",
            re.I,
        ),
        "references credential/secret exfiltration",
    ),
    (
        re.compile(
            r"\bbefore (using|calling|invoking) this tool\b.{0,60}\b(you must|always|"
            r"first)\b",
            re.I,
        ),
        "injects a mandatory pre-step into the agent",
    ),
    (
        re.compile(r"\b(read|open|cat|fetch)\b.{0,30}\b(then|and)\b.{0,30}\bsend\b", re.I),
        "chains a read-then-exfiltrate instruction",
    ),
]


@dataclass
class ScanFinding:
    tool: str
    where: str  # description | name | schema
    pattern: str  # what triggered
    severity: str  # high | med


def _scan_text(tool: str, where: str, text: str) -> list[ScanFinding]:
    out: list[ScanFinding] = []
    for rx, why in _POISON:
        if rx.search(text or ""):
            out.append(ScanFinding(tool=tool, where=where, pattern=why, severity="high"))
    return out


def scan_tool(
    name: str, description: str, schema: dict[str, Any] | None = None
) -> list[ScanFinding]:
    """Flag poisoning/injection patterns in a tool's metadata. Pure."""
    out = _scan_text(name, "name", name) + _scan_text(name, "description", description)
    if schema:
        # injection can hide in parameter descriptions too
        try:
            blob = json.dumps(schema)
        except Exception:
            blob = str(schema)
        out += _scan_text(name, "schema", blob)
    return out


def scan_result(text: str) -> list[ScanFinding]:
    """Scan an MCP tool RESULT (untrusted RETURNED data) for injected instructions /
    exfiltration — indirect / second-order prompt injection (ASI02). Static description
    scanning (scan_tool) misses this; a benign-looking server can still return poisoned
    data at runtime, so results are scanned at the point of return."""
    return _scan_text("<result>", "result", text)


def scan_tools(tools: list[dict[str, Any]]) -> dict[str, list[ScanFinding]]:
    res: dict[str, list[ScanFinding]] = {}
    for t in tools:
        f = scan_tool(
            str(t.get("name", "")),
            str(t.get("description", "")),
            t.get("schema") or t.get("inputSchema"),
        )
        if f:
            res[str(t.get("name", ""))] = f
    return res


def is_clean(tools: list[dict[str, Any]]) -> bool:
    return not scan_tools(tools)


def manifest_hash(tools: list[dict[str, Any]]) -> str:
    """A stable hash of a server's tool manifest (name+description+schema), for pinning."""
    items = sorted(
        (
            str(t.get("name", "")),
            str(t.get("description", "")),
            json.dumps(t.get("schema") or t.get("inputSchema") or {}, sort_keys=True),
        )
        for t in tools
    )
    return hashlib.sha256(json.dumps(items).encode("utf-8")).hexdigest()


@dataclass
class PinResult:
    ok: bool
    pinned: str
    current: str
    drifted: bool  # the manifest changed since it was approved (possible rug-pull)


def verify_pin(pinned_hash: str, current_tools: list[dict[str, Any]]) -> PinResult:
    """Re-verify a server's manifest against the pinned hash. drifted=True => quarantine."""
    cur = manifest_hash(current_tools)
    drifted = bool(pinned_hash) and pinned_hash != cur
    return PinResult(ok=not drifted, pinned=pinned_hash, current=cur, drifted=drifted)
