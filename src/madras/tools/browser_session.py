"""Headless-browser session for governed browser automation.

The 2025-26 robustness winner is DOM/accessibility-tree-driven control (not
vision): snapshot the page as a compact ARIA tree, then act on elements by
ROLE + accessible NAME (stable, low-token, free-model-friendly). Engine is
Playwright/Chromium.

Playwright drives Chromium over a Node subprocess that needs a subprocess-capable
event loop. The rest of the app may run on Windows' *selector* loop (set for SSL
compatibility), which can't spawn subprocesses. So the whole session lives in ONE
dedicated worker thread running its OWN *proactor* loop and Playwright's async
API; async tool methods bridge their coroutine onto that loop via
``run_coroutine_threadsafe``. This works under any caller loop (selector or
proactor) and in tests.
"""

from __future__ import annotations

import asyncio
import sys
import threading
from collections.abc import Callable, Coroutine
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_ACT_TIMEOUT_MS = 8_000  # Playwright per-action timeout


@dataclass
class BrowserError(Exception):
    message: str

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.message


class BrowserSession:
    """A headless Chromium session driven from a single owner thread + loop."""

    def __init__(self, *, headless: bool = True) -> None:
        self._headless = headless
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._page: Any = None
        self._browser: Any = None
        self._ready = threading.Event()
        self._stop_event: asyncio.Event | None = None
        self._start_error: BaseException | None = None

    # -- lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout=60.0):
            raise BrowserError("browser failed to start (timeout)")
        if self._start_error is not None:
            err = self._start_error
            raise BrowserError(f"browser failed to start: {type(err).__name__}: {err!r}")

    def _run(self) -> None:
        # Dedicated proactor loop (Windows) so Playwright can spawn its driver.
        loop = asyncio.ProactorEventLoop() if sys.platform == "win32" else asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        try:
            loop.run_until_complete(self._main())
        except BaseException as exc:
            if not self._ready.is_set():
                self._start_error = exc
                self._ready.set()
        finally:
            loop.close()

    async def _main(self) -> None:
        from playwright.async_api import async_playwright

        self._stop_event = asyncio.Event()
        async with async_playwright() as p:
            self._browser = await p.chromium.launch(headless=self._headless)
            self._page = await self._browser.new_page()
            self._page.set_default_timeout(_ACT_TIMEOUT_MS)
            self._ready.set()
            await self._stop_event.wait()
            await self._browser.close()

    def stop(self) -> None:
        if self._thread is None or self._loop is None:
            return
        ev = self._stop_event
        if ev is not None:
            self._loop.call_soon_threadsafe(ev.set)
        self._thread.join(timeout=10.0)
        self._thread = None

    # -- op submission ------------------------------------------------------
    async def _submit(self, coro_factory: Callable[[Any], Coroutine[Any, Any, Any]]) -> Any:
        if self._loop is None or self._page is None:
            raise BrowserError("browser session not started")
        fut = asyncio.run_coroutine_threadsafe(coro_factory(self._page), self._loop)
        return await asyncio.wrap_future(fut)

    # -- governed operations (return plain data; raise on failure) ----------
    async def goto(self, url: str) -> str:
        async def _op(page: Any) -> str:
            await page.goto(url, wait_until="domcontentloaded")
            return (await page.title()) or page.url

        return await self._submit(_op)

    async def snapshot(self) -> str:
        return await self._submit(lambda page: page.locator("body").aria_snapshot())

    async def read_text(self) -> str:
        return await self._submit(lambda page: page.inner_text("body"))

    async def current_url(self) -> str:
        async def _op(page: Any) -> str:
            return page.url

        return await self._submit(_op)

    async def click(self, role: str, name: str) -> str:
        async def _op(page: Any) -> str:
            await page.get_by_role(role, name=name).first.click()
            return page.url

        return await self._submit(_op)

    async def fill(self, role: str, name: str, text: str) -> None:
        async def _op(page: Any) -> None:
            await page.get_by_role(role, name=name).first.fill(text)

        await self._submit(_op)


_active: ContextVar[BrowserSession | None] = ContextVar("madras_browser_session", default=None)


def set_active_browser(session: BrowserSession | None) -> None:
    _active.set(session)


def get_active_browser() -> BrowserSession | None:
    return _active.get()


# Lazily-built shared session for the cockpit loop (one browser per process;
# launching Chromium per request is too slow). None if Playwright/Chromium is
# unavailable so the tools degrade gracefully.
_shared: dict[str, Any] = {"session": None}
_shared_lock = threading.Lock()


def get_or_start_shared() -> BrowserSession | None:
    with _shared_lock:
        if _shared["session"] is not None:
            return _shared["session"]
        try:
            sess = BrowserSession(headless=True)
            sess.start()
        except Exception:
            return None
        _shared["session"] = sess
        return sess


def shutdown_shared() -> None:
    with _shared_lock:
        sess = _shared["session"]
        if sess is not None:
            try:
                sess.stop()
            except Exception:
                pass
            _shared["session"] = None
