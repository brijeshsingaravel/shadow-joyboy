"""Governed browser-automation tools (accessibility-tree driven).

navigate → snapshot (compact ARIA tree) → act by ROLE + accessible NAME. This is
the 2025-26 robustness winner (DOM-driven beats vision by 12-17pp), low-token,
and works on free text-only models. Engine: Playwright/Chromium in a persistent
BrowserSession. Toolset 'browser' is dangerous (drives a live browser on the open
web) → approval-gated in DEFAULT mode like shell; every call is rank-gated +
8-dim-eval'd + audited.
"""

from __future__ import annotations

from typing import Any

from madras.models.agent_config import Rank
from madras.security.net_policy import NetPolicy
from madras.security.rails import scan_retrieval
from madras.tools.browser_session import get_active_browser
from madras.tools.registry import ToolResult, tool

_MAX_SNAPSHOT = 6_000
_MAX_TEXT = 6_000

# s46: tools/governed_browser.py's GovernedBrowser design (egress-check every navigation
# via NetPolicy BEFORE loading, SSRF/non-allowed-domain block) never made it into the
# LIVE Playwright-backed tools below -- browser_navigate called sess.goto(url) straight
# through with no egress gate at all. Reusing that policy here directly rather than
# adopting GovernedBrowser's alternate browser-use backend (this one already works).
_NET_POLICY = NetPolicy()


def _no_session() -> ToolResult:
    return ToolResult(ok=False, error="no browser session active")


@tool(
    name="browser_navigate",
    toolset="browser",
    rank_required=Rank.INTERN,
    description=(
        "Open a URL in the headless browser. Returns the page title + URL. Follow "
        "with browser_snapshot to see interactable elements."
    ),
    parameters={
        "type": "object",
        "properties": {"url": {"type": "string", "description": "URL to open"}},
        "required": ["url"],
    },
)
async def browser_navigate(args: dict[str, Any]) -> ToolResult:
    sess = get_active_browser()
    if sess is None:
        return _no_session()
    url = str(args.get("url", "")).strip()
    if not url:
        return ToolResult(ok=False, error="url is required")
    verdict = _NET_POLICY.check(url)
    if not verdict.allow:
        return ToolResult(ok=False, error=f"navigation blocked: {verdict.reason}")
    try:
        title = await sess.goto(url)
        current = await sess.current_url()
    except Exception as exc:
        return ToolResult(ok=False, error=f"navigate failed: {type(exc).__name__}: {exc}")
    return ToolResult(ok=True, content=f"{title}\n{current}", extras={"url": current})


@tool(
    name="browser_snapshot",
    toolset="browser",
    rank_required=Rank.INTERN,
    description=(
        "Return the current page as a compact accessibility tree: each line is "
        "'role \"name\"'. Use the role + name to target browser_click / browser_type."
    ),
    parameters={"type": "object", "properties": {}},
)
async def browser_snapshot(args: dict[str, Any]) -> ToolResult:
    sess = get_active_browser()
    if sess is None:
        return _no_session()
    try:
        snap = await sess.snapshot()
    except Exception as exc:
        return ToolResult(ok=False, error=f"snapshot failed: {type(exc).__name__}: {exc}")
    scanned = await scan_retrieval((snap or "")[:_MAX_SNAPSHOT])
    return ToolResult(ok=True, content=scanned)


@tool(
    name="browser_read",
    toolset="browser",
    rank_required=Rank.INTERN,
    description="Return the visible text of the current page (boilerplate included).",
    parameters={"type": "object", "properties": {}},
)
async def browser_read(args: dict[str, Any]) -> ToolResult:
    sess = get_active_browser()
    if sess is None:
        return _no_session()
    try:
        text = await sess.read_text()
    except Exception as exc:
        return ToolResult(ok=False, error=f"read failed: {type(exc).__name__}: {exc}")
    scanned = await scan_retrieval((text or "")[:_MAX_TEXT])
    return ToolResult(ok=True, content=scanned)


@tool(
    name="browser_click",
    toolset="browser",
    rank_required=Rank.INTERN,
    description=(
        "Click an element identified by its accessibility role and name (from "
        "browser_snapshot), e.g. role='button' name='Sign in'."
    ),
    parameters={
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role, e.g. button, link"},
            "name": {"type": "string", "description": "accessible name (visible label)"},
        },
        "required": ["role", "name"],
    },
)
async def browser_click(args: dict[str, Any]) -> ToolResult:
    sess = get_active_browser()
    if sess is None:
        return _no_session()
    role = str(args.get("role", "")).strip()
    name = str(args.get("name", "")).strip()
    if not role or not name:
        return ToolResult(ok=False, error="role and name are required")
    try:
        current = await sess.click(role, name)
    except Exception as exc:
        return ToolResult(
            ok=False,
            error=f"[NO-ELEMENT] click failed for {role} {name!r}: {type(exc).__name__}; "
            "re-snapshot and use an exact role+name.",
        )
    return ToolResult(ok=True, content=f"clicked {role} {name!r}", extras={"url": current})


@tool(
    name="browser_type",
    toolset="browser",
    rank_required=Rank.INTERN,
    description=(
        "Type text into a field identified by accessibility role and name (from "
        "browser_snapshot), e.g. role='textbox' name='Search'. Replaces existing value."
    ),
    parameters={
        "type": "object",
        "properties": {
            "role": {"type": "string", "description": "ARIA role, usually 'textbox'"},
            "name": {"type": "string", "description": "accessible name of the field"},
            "text": {"type": "string", "description": "text to enter"},
        },
        "required": ["role", "name", "text"],
    },
)
async def browser_type(args: dict[str, Any]) -> ToolResult:
    sess = get_active_browser()
    if sess is None:
        return _no_session()
    role = str(args.get("role", "")).strip()
    name = str(args.get("name", "")).strip()
    text = str(args.get("text", ""))
    if not role or not name:
        return ToolResult(ok=False, error="role and name are required")
    try:
        await sess.fill(role, name, text)
    except Exception as exc:
        return ToolResult(
            ok=False,
            error=f"[NO-ELEMENT] type failed for {role} {name!r}: {type(exc).__name__}; "
            "re-snapshot and use an exact role+name.",
        )
    return ToolResult(ok=True, content=f"typed into {role} {name!r}")
