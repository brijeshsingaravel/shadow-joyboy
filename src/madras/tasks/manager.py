"""In-memory TaskManager — the pure core of cockpit background/async tasks.

No FastAPI, no LLM, no DB. Holds tasks in process memory, tracks lifecycle
transitions, and keeps a per-task append-only event log. A future SSE stream
awaits new events via :meth:`TaskManager.wait_for_event`.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"


class Task(BaseModel):
    id: str
    session_id: str
    title: str
    prompt: str
    status: TaskStatus = TaskStatus.QUEUED
    events: list[dict[str, Any]] = Field(default_factory=list[dict[str, Any]])
    result: str | None = None
    error: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=_now)
    started_at: datetime | None = None
    ended_at: datetime | None = None


class TaskManager:
    """Process-local registry of :class:`Task` records, in insertion order."""

    def __init__(
        self,
        *,
        on_complete: Callable[[Task], Awaitable[None]] | None = None,
    ) -> None:
        self._tasks: dict[str, Task] = {}
        # Per-task condition: notifies awaiters when a new event is appended.
        self._conditions: dict[str, asyncio.Condition] = {}
        # Hold strong refs to in-flight notify tasks so they are not GC'd.
        self._notify_tasks: set[asyncio.Task[None]] = set()
        # Completion-driven re-invocation: fired when a task reaches a terminal state
        # (done/error). The cockpit wires this to re-enter the agent loop with the result
        # — so background work NOTIFIES instead of the agent polling. Best-effort.
        self._on_complete = on_complete

    def set_on_complete(self, cb: Callable[[Task], Awaitable[None]] | None) -> None:
        self._on_complete = cb

    async def _fire_complete(self, task_id: str) -> None:
        if self._on_complete is None:
            return
        task = self._tasks.get(task_id)
        if task is None:
            return
        try:
            await self._on_complete(task)
        except Exception:
            pass  # re-invocation must never break the task lifecycle

    def create(self, *, session_id: str, title: str, prompt: str, **meta: Any) -> Task:
        task_id = f"task_{uuid.uuid4().hex[:8]}"
        task = Task(
            id=task_id,
            session_id=session_id,
            title=title,
            prompt=prompt,
            meta=dict(meta),
        )
        self._tasks[task_id] = task
        self._conditions[task_id] = asyncio.Condition()
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def list(self) -> list[Task]:
        return list(self._tasks.values())

    def _require(self, task_id: str) -> Task:
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"unknown task id: {task_id!r}")
        return task

    def events(self, task_id: str, since: int = 0) -> list[dict[str, Any]]:
        return self._require(task_id).events[since:]

    def add_event(self, task_id: str, event: dict[str, Any]) -> None:
        task = self._require(task_id)
        task.events.append(dict(event))
        self._notify(task_id)

    def mark_running(self, task_id: str) -> None:
        task = self._require(task_id)
        task.status = TaskStatus.RUNNING
        task.started_at = _now()
        self._notify(task_id)

    def mark_done(self, task_id: str, *, result: str) -> None:
        task = self._require(task_id)
        task.status = TaskStatus.DONE
        task.result = result
        task.ended_at = _now()
        self._notify(task_id)

    def mark_error(self, task_id: str, *, error: str) -> None:
        task = self._require(task_id)
        task.status = TaskStatus.ERROR
        task.error = error
        task.ended_at = _now()
        self._notify(task_id)

    def mark_cancelled(self, task_id: str) -> None:
        task = self._require(task_id)
        task.status = TaskStatus.CANCELLED
        task.ended_at = _now()
        self._notify(task_id)

    async def launch(
        self,
        task_id: str,
        runner: Callable[[Callable[[dict[str, Any]], None]], Awaitable[str]],
    ) -> None:
        """Run a governed ``runner`` to completion, forwarding its events.

        ``runner`` is an async callable taking a single ``on_event`` callback
        (sync) that appends to this task's event log. The task is marked
        ``running`` before the runner is invoked; on success it is marked
        ``done`` with the runner's return value, on any exception ``error``
        with the exception string. Events emitted before a raise are kept.

        ``asyncio.CancelledError`` is re-raised after marking the task
        ``cancelled`` — cancellation is cooperative and must propagate so the
        awaiting layer (the future FastAPI background task) can unwind.
        """
        self._require(task_id)

        def on_event(event: dict[str, Any]) -> None:
            self.add_event(task_id, event)

        self.mark_running(task_id)
        try:
            result = await runner(on_event)
        except asyncio.CancelledError:
            self.mark_cancelled(task_id)
            raise
        except Exception as exc:
            self.mark_error(task_id, error=str(exc))
            await self._fire_complete(task_id)  # re-invoke: handle the failure
            return
        self.mark_done(task_id, result=result)
        await self._fire_complete(task_id)  # re-invoke: continue with the result (no poll)

    def _notify(self, task_id: str) -> None:
        cond = self._conditions.get(task_id)
        if cond is None:
            return

        async def _wake() -> None:
            async with cond:
                cond.notify_all()

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # No loop (sync context) — nothing is awaiting.
        notify_task = loop.create_task(_wake())
        self._notify_tasks.add(notify_task)
        notify_task.add_done_callback(self._notify_tasks.discard)

    async def wait_for_event(self, task_id: str, since: int) -> list[dict[str, Any]]:
        """Await and return events at index >= ``since``.

        Returns immediately if a backlog already exists; otherwise blocks until
        the next :meth:`add_event` (or any state change) wakes the condition.
        """
        task = self._require(task_id)
        cond = self._conditions[task_id]
        async with cond:
            await cond.wait_for(lambda: len(task.events) > since)
            return task.events[since:]
