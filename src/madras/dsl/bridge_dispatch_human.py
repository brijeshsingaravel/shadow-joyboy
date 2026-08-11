"""Phase U -- the human transport's dispatcher: takes a real `BridgeManifest`
(transport=human) and actually dispatches it, generalizing the same "reuse a real, already-proven
shape" discipline `dispatch_in_process`/`dispatch_network`/`dispatch_shared_memory` already used
-- except here there IS no existing marketplace task-assignment mechanism to reuse
(`madras.tasks.manager.TaskManager` is the cockpit's own background-task tracker, a different
domain: session_id/prompt-shaped, not a human-contributor task queue).

**Honest v0 scope, disclosed, not glossed over:** only `notify_via="queue"` has a real mechanism
here -- a genuine, working in-process task queue (enqueue/claim/complete). `ui_form` (a real
frontend form page) and `email` (real email-sending infra) don't exist yet anywhere in this
codebase; dispatching either raises a clear, honest error rather than silently no-op'ing or
faking success. Real future work, not this row's scope.
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from madras.models.bridge_manifest import BridgeManifest, Transport


class HumanTaskStatus(str, Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    DONE = "done"


class HumanTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    manifest_name: str
    task_description: str
    args: dict[str, Any] = Field(default_factory=dict)
    status: HumanTaskStatus = HumanTaskStatus.PENDING
    result: dict[str, Any] | None = None


class UnknownHumanTaskError(KeyError):
    """`claim`/`complete` referenced a task id the queue has never seen."""


class HumanTaskQueue:
    """A real, working in-process queue -- enqueue, claim (pending -> claimed), complete
    (claimed -> done, with a real result). Process-local (no persistence yet), mirroring
    `bridge_dispatch_shared_memory`'s own "shared across calls within this process" scope."""

    def __init__(self) -> None:
        self._tasks: dict[str, HumanTask] = {}

    def enqueue(self, manifest: BridgeManifest, args: dict[str, Any] | None) -> HumanTask:
        iface = manifest.human_interface
        assert iface is not None  # enforced by BridgeManifest's own transport/interface validator
        task = HumanTask(
            id=f"human_task_{uuid.uuid4().hex[:8]}",
            manifest_name=manifest.name,
            task_description=iface.task_description,
            args=args or {},
        )
        self._tasks[task.id] = task
        return task

    def pending(self) -> list[HumanTask]:
        return [t for t in self._tasks.values() if t.status is HumanTaskStatus.PENDING]

    def claim(self, task_id: str) -> HumanTask:
        task = self._get(task_id)
        task.status = HumanTaskStatus.CLAIMED
        return task

    def complete(self, task_id: str, result: dict[str, Any]) -> HumanTask:
        task = self._get(task_id)
        task.status = HumanTaskStatus.DONE
        task.result = result
        return task

    def _get(self, task_id: str) -> HumanTask:
        task = self._tasks.get(task_id)
        if task is None:
            raise UnknownHumanTaskError(task_id)
        return task


_default_queue = HumanTaskQueue()


def dispatch_human(
    manifest: BridgeManifest,
    args: dict[str, Any] | None = None,
    *,
    queue: HumanTaskQueue | None = None,
) -> HumanTask:
    """Enqueue a real human task for `manifest` -- only `notify_via="queue"` is real; any other
    value raises, honestly, rather than pretending to notify anyone."""
    if manifest.transport is not Transport.HUMAN:
        raise ValueError(f"dispatch_human only handles transport=human, got {manifest.transport!r}")
    iface = manifest.human_interface
    assert iface is not None  # enforced by BridgeManifest's own transport/interface validator
    if iface.notify_via != "queue":
        raise ValueError(
            f"notify_via={iface.notify_via!r} isn't built yet -- only 'queue' has a real "
            "dispatch mechanism so far"
        )
    return (queue or _default_queue).enqueue(manifest, args)


__all__ = [
    "HumanTask",
    "HumanTaskQueue",
    "HumanTaskStatus",
    "UnknownHumanTaskError",
    "dispatch_human",
]
