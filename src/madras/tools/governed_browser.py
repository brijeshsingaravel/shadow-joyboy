"""Governed deep browser navigation — drive a web agent, but every step governed.

Backend finalized: **browser-use** (MIT, Python, SOTA 89% WebVoyager) — a native fit, no node
bridge. `GovernedBrowser` wraps a `BrowserBackend` ABC so the off-the-shelf web agent's actions
pass through Madras's gates: **navigations are egress-checked** by the [[Network Egress Policy]]
(B50, blocks SSRF / non-allowed domains BEFORE loading), **mutating actions** (click/fill/submit/
download) are **approval-gated**, read-only actions (extract/screenshot/scroll) pass, and all are
audited. The backend is injectable → pure + deterministic; the live browser-use wiring is a thin
adapter.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

from madras.security.net_policy import NetPolicy

NAVIGATE = frozenset({"navigate", "back", "forward"})
READ_ONLY = frozenset({"extract", "screenshot", "scroll", "get_text", "get_state"})
MUTATING = frozenset({"click", "fill", "submit", "download", "select", "press"})
ACTION_TYPES = NAVIGATE | READ_ONLY | MUTATING


@dataclass
class BrowserAction:
    type: str
    url: str = ""
    selector: str = ""
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class BrowserResult:
    ok: bool
    output: str = ""
    error: str | None = None


@runtime_checkable
class BrowserBackend(Protocol):
    async def execute(self, action: BrowserAction) -> BrowserResult: ...


@dataclass
class GovernedBrowser:
    backend: BrowserBackend
    net_policy: NetPolicy | None = None
    approve: Callable[[BrowserAction], bool] | None = None
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def do(self, action: BrowserAction) -> BrowserResult:
        t = action.type
        if t not in ACTION_TYPES:
            return BrowserResult(False, error=f"unknown browser action '{t}'")

        if t in NAVIGATE and action.url and self.net_policy is not None:
            verdict = self.net_policy.check(action.url)
            if not verdict.allow:
                self._audit(
                    {"event": "blocked", "action": t, "url": action.url, "reason": verdict.reason}
                )
                return BrowserResult(False, error=f"navigation blocked: {verdict.reason}")

        if t in MUTATING and self.approve is not None and not self.approve(action):
            self._audit({"event": "denied", "action": t, "selector": action.selector})
            return BrowserResult(False, error=f"action '{t}' denied (approval)")

        self._audit({"event": "action", "action": t})
        return await self.backend.execute(action)


class BrowserUseBackend:
    """Adapter over browser-use (MIT). Maps a `BrowserAction` to an injected browser-use session
    (or a fake in tests); `connect()` lazy-imports the optional `browser_use` SDK so importing
    this module never requires it. Exact session method names may need a tweak when wired live."""

    def __init__(self, session: Any) -> None:
        self._s = session

    @classmethod
    async def connect(cls, **kwargs: Any) -> BrowserUseBackend:
        try:
            # browser-use SDK (optional dep)
            from browser_use import BrowserSession  # type: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "browser-use is not installed — `pip install browser-use` (MIT) to wire the live "
                "deep-navigation backend"
            ) from exc
        session = cast("Any", BrowserSession(**kwargs))
        await session.start()
        return cls(session)

    async def execute(self, action: BrowserAction) -> BrowserResult:
        s, t = self._s, action.type
        try:
            if t == "navigate":
                out = await s.navigate(action.url)
            elif t in ("back", "forward"):
                out = await getattr(s, t)()
            elif t == "click":
                out = await s.click(action.selector)
            elif t == "fill":
                out = await s.fill(action.selector, action.text)
            elif t == "get_text":
                out = await s.get_text(action.selector)
            elif t == "extract":
                out = await s.extract(**action.detail)
            elif t == "screenshot":
                out = await s.screenshot()
            elif t == "scroll":
                out = await s.scroll(**action.detail)
            else:
                out = await getattr(s, t)(**action.detail)
        except Exception as exc:
            return BrowserResult(False, error=f"{type(exc).__name__}: {exc}")
        return BrowserResult(True, output="" if out is None else str(out))
