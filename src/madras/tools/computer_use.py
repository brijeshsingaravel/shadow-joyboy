"""Governed computer-use — every OS action behind one approval + audit gate.

Native computer-use (screenshot/click/type/scroll) is the highest-trust surface there is, so
Madras governs it the way it governs the sandbox: a `ComputerBackend` ABC (the real CUA / Chrome /
pyautogui driver implements it) behind a `GovernedComputer` that **approval-gates every mutating
action** (read-only screenshots pass; clicks/typing/keys/drags require approval) and **audits all
of them**. The backend is injectable → pure + deterministic here; the heavy native driver is a
thin adapter, never imported by the agent directly.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, cast, runtime_checkable

ACTION_TYPES = frozenset(
    {
        "screenshot",
        "cursor_position",
        "click",
        "double_click",
        "right_click",
        "type",
        "key",
        "scroll",
        "move",
        "drag",
    }
)
READ_ONLY = frozenset({"screenshot", "cursor_position"})


@dataclass
class ComputerAction:
    type: str
    x: int | None = None
    y: int | None = None
    text: str = ""
    detail: dict[str, Any] = field(default_factory=dict[str, Any])


@dataclass
class ActionResult:
    ok: bool
    output: str = ""
    error: str | None = None


@runtime_checkable
class ComputerBackend(Protocol):
    async def execute(self, action: ComputerAction) -> ActionResult: ...


@dataclass
class GovernedComputer:
    backend: ComputerBackend
    approve: Callable[[ComputerAction], bool] | None = None
    audit: Callable[[dict[str, Any]], None] | None = None

    def _audit(self, record: dict[str, Any]) -> None:
        if self.audit is not None:
            self.audit(record)

    async def do(self, action: ComputerAction) -> ActionResult:
        if action.type not in ACTION_TYPES:
            return ActionResult(False, error=f"unknown computer action '{action.type}'")
        mutating = action.type not in READ_ONLY
        if mutating and self.approve is not None and not self.approve(action):
            self._audit({"event": "denied", "action": action.type})
            return ActionResult(False, error=f"action '{action.type}' denied (approval)")
        self._audit({"event": "action", "action": action.type, "mutating": mutating})
        return await self.backend.execute(action)


class CuaBackend:
    """Adapter over the finalized backend — `trycua/cua` (MIT, cross-OS sandboxed computer-use),
    with UI-TARS-1.5 (Apache-2.0) as the local grounding model (composite-agent pattern). Maps a
    `ComputerAction` to the cua `Computer.interface` async methods. The cua interface is INJECTED
    (a real `computer.interface`, or a fake in tests); `connect()` lazy-imports the optional `cua`
    SDK so importing this module never requires it.
    """

    def __init__(self, interface: Any) -> None:
        self._iface = interface

    @classmethod
    async def connect(cls, **kwargs: Any) -> CuaBackend:
        try:
            # cua SDK (optional dep)
            from computer import Computer  # type: ignore[reportMissingImports]
        except ImportError as exc:  # pragma: no cover - exercised only when cua is absent
            raise ImportError(
                "the 'cua' SDK is not installed — `pip install cua` (trycua/cua, MIT) to wire "
                "the live computer-use backend"
            ) from exc
        computer = cast("Any", Computer(**kwargs))
        await computer.run()
        return cls(computer.interface)

    async def execute(self, action: ComputerAction) -> ActionResult:
        iface = self._iface
        t = action.type
        try:
            if t in ("screenshot", "cursor_position"):
                out = await (
                    iface.screenshot() if t == "screenshot" else iface.get_cursor_position()
                )
            elif t == "click":
                out = await iface.left_click(action.x, action.y)
            elif t == "right_click":
                out = await iface.right_click(action.x, action.y)
            elif t == "double_click":
                out = await iface.double_click(action.x, action.y)
            elif t == "move":
                out = await iface.move_cursor(action.x, action.y)
            elif t == "type":
                out = await iface.type_text(action.text)
            elif t == "key":
                out = await iface.press_key(action.text or action.detail.get("key", ""))
            elif t == "scroll":
                out = await iface.scroll(action.x, action.y, **action.detail)
            elif t == "drag":
                out = await iface.drag(**action.detail)
            else:
                return ActionResult(False, error=f"unmapped action '{t}'")
        except Exception as exc:  # the cua call failed
            return ActionResult(False, error=f"{type(exc).__name__}: {exc}")
        return ActionResult(True, output="" if out is None else str(out))
