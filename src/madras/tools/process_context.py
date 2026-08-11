"""Background-process registry + per-run context (shell-execution capability).

Backs the governed shell_start / shell_output / shell_kill tools. A long-running
command (dev server, watcher, build) is launched with subprocess.Popen and its
combined stdout/stderr is drained by a daemon thread into a bounded thread-safe
buffer; shell_output returns only the NEW bytes since the last poll, shell_kill
terminates it. Pure threads (no asyncio subprocess) so it behaves identically on
Windows' selector loop and POSIX.

Exposed via a ContextVar like memory_context / plan_context: the cockpit loop
sets an active registry when the shell toolset is in play and tears it down
(killing any survivors) on exit.
"""

from __future__ import annotations

import subprocess
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from madras.config import settings

# Keep at most this many bytes of buffered output per process (ring-trim oldest).
_MAX_BUFFER_BYTES = 256_000


def _workspace_root() -> Path:
    root = (
        Path(settings.madras_workspace)
        if settings.madras_workspace
        else Path(__file__).resolve().parents[3] / "workspace"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


@dataclass
class ProcStatus:
    found: bool
    running: bool = False
    exit_code: int | None = None
    new_output: str = ""


@dataclass
class _Proc:
    proc_id: str
    cmd: str
    popen: subprocess.Popen[str]
    buffer: list[str] = field(default_factory=list[str])
    read_offset: int = 0  # index into buffer already returned to the agent
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader: threading.Thread | None = None


class ProcessRegistry:
    """Per-run table of background OS processes, confined to the workspace cwd."""

    def __init__(self, *, workspace: Path | None = None, max_processes: int = 5) -> None:
        self._workspace = (workspace or _workspace_root()).resolve()
        self._max = max_processes
        self._procs: dict[str, _Proc] = {}
        self._seq = 0
        self._guard = threading.Lock()

    def _drain(self, p: _Proc) -> None:
        """Daemon-thread body: read combined output line-by-line into the buffer."""
        stream = p.popen.stdout
        if stream is None:
            return
        for line in stream:
            with p.lock:
                p.buffer.append(line)
                # Ring-trim if the buffer grows past the cap (drop oldest, keep offset sane).
                total = sum(len(s) for s in p.buffer)
                while total > _MAX_BUFFER_BYTES and len(p.buffer) > 1:
                    dropped = p.buffer.pop(0)
                    total -= len(dropped)
                    if p.read_offset > 0:
                        p.read_offset -= 1

    def start(self, cmd: str) -> str:
        """Launch ``cmd`` in the background; return its proc_id."""
        cmd = cmd.strip()
        if not cmd:
            raise ValueError("cmd is required")
        with self._guard:
            live = sum(1 for p in self._procs.values() if p.popen.poll() is None)
            if live >= self._max:
                raise RuntimeError(
                    f"max background processes ({self._max}) already running; "
                    "kill one with shell_kill before starting another"
                )
            self._seq += 1
            proc_id = f"proc-{self._seq}"
        # shell=True is the tool's contract (run an arbitrary shell command);
        # gated by the rank-gate + approval engine + sandbox-active check.
        popen = subprocess.Popen(
            cmd,
            shell=True,
            cwd=str(self._workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        p = _Proc(proc_id=proc_id, cmd=cmd, popen=popen)
        reader = threading.Thread(target=self._drain, args=(p,), daemon=True)
        p.reader = reader
        reader.start()
        with self._guard:
            self._procs[proc_id] = p
        return proc_id

    def read(self, proc_id: str) -> ProcStatus:
        """Return new output since the last read + running/exit status."""
        p = self._procs.get(proc_id)
        if p is None:
            return ProcStatus(found=False)
        with p.lock:
            new = "".join(p.buffer[p.read_offset :])
            p.read_offset = len(p.buffer)
        code = p.popen.poll()
        return ProcStatus(
            found=True,
            running=code is None,
            exit_code=code,
            new_output=new,
        )

    def kill(self, proc_id: str) -> bool:
        """Terminate a background process. True if it existed."""
        p = self._procs.get(proc_id)
        if p is None:
            return False
        if p.popen.poll() is None:
            try:
                p.popen.terminate()
                try:
                    p.popen.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    p.popen.kill()
            except Exception:
                pass
        return True

    def list(self) -> list[dict[str, object]]:
        """Snapshot of known processes (proc_id, cmd, running, exit_code)."""
        out: list[dict[str, object]] = []
        for p in self._procs.values():
            code = p.popen.poll()
            out.append(
                {
                    "proc_id": p.proc_id,
                    "cmd": p.cmd,
                    "running": code is None,
                    "exit_code": code,
                }
            )
        return out

    def shutdown(self) -> None:
        """Kill all surviving processes (teardown on run exit)."""
        for proc_id in list(self._procs):
            self.kill(proc_id)


_active: ContextVar[ProcessRegistry | None] = ContextVar("madras_process_registry", default=None)


def set_active_processes(reg: ProcessRegistry | None) -> None:
    _active.set(reg)


def get_active_processes() -> ProcessRegistry | None:
    return _active.get()
