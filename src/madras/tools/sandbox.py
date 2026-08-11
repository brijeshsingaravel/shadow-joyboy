"""Sandbox abstraction for dangerous tools.

Backends (the scaling ladder — see plan): local (dev/trusted, no isolation) ->
docker (default for real use: workspace-mounted, network off, resource-limited,
non-root). Firecracker/E2B for multi-tenant later, behind this same ABC.

Windows note: WindowsSelectorEventLoop (set by conftest for SSL compat) does not
support asyncio subprocess. LocalSandbox falls back to `subprocess.run` in a
thread-pool for Windows compatibility; DockerSandbox uses `docker` CLI the same
way. On Linux/macOS, asyncio.create_subprocess_shell is used directly.
"""

from __future__ import annotations

import abc
import asyncio
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from madras.config import settings

if TYPE_CHECKING:
    from madras.security.cred_broker import CredentialBroker
    from madras.security.cred_proxy_server import CredentialProxyServer


@dataclass
class CommandResult:
    ok: bool  # exit_code == 0
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    error: str | None = None  # harness-level error (timeout, backend failure)


@dataclass
class BackgroundHandle:
    """The reconnect handle for a dispatched background command: durable across
    disconnect/reconnect (a new session, a new process) — unlike ProcessRegistry's
    in-memory local processes, this is meant to survive the current run ending."""

    sandbox_id: str
    pid: int
    job_id: str


@dataclass
class BackgroundStatus:
    running: bool
    ok: bool = False
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    error: str | None = None


@dataclass
class SandboxPolicy:
    """Declarable, structured sandbox limits (Codex-style: workspaceWrite + writableRoots +
    networkAccess). The workspace is ALWAYS writable; `writable_roots` adds extra allowed
    roots; `network_access` gates egress (default DENY → Docker `--network none`)."""

    writable_roots: list[Path] = field(default_factory=list[Path])
    network_access: bool = False
    max_runtime_secs: float = 120.0


def _workspace_root() -> Path:
    root = (
        Path(settings.madras_workspace)
        if settings.madras_workspace
        else Path(__file__).resolve().parents[3] / "workspace"
    )
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


class Sandbox(abc.ABC):
    def __init__(
        self, *, session_id: str, workspace: Path | None = None, policy: SandboxPolicy | None = None
    ) -> None:
        self.session_id = session_id
        self.workspace = (workspace or _workspace_root()).resolve()
        self.policy = policy or SandboxPolicy()
        # The workspace is always writable; policy adds extra roots.
        self._writable_roots: list[Path] = [
            self.workspace,
            *(Path(r).resolve() for r in self.policy.writable_roots),
        ]

    def _resolve_writable(self, requested: str) -> Path | None:
        """Resolve a path and confirm it's under a writable root (workspace + policy roots).
        Relative paths resolve under the workspace; absolute paths must fall under a root."""
        req = Path(requested)
        candidate = req.resolve() if req.is_absolute() else (self.workspace / req).resolve()
        for root in self._writable_roots:
            try:
                candidate.relative_to(root)
                return candidate
            except ValueError:
                continue
        return None

    @abc.abstractmethod
    async def start(self) -> None: ...

    @abc.abstractmethod
    async def run_command(self, cmd: str, *, timeout: float | None = None) -> CommandResult: ...

    @abc.abstractmethod
    async def stop(self) -> None: ...

    async def write_file(self, path: str, content: str) -> CommandResult:
        target = self._resolve_writable(path)
        if target is None:
            return CommandResult(ok=False, error="path not in a writable root")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return CommandResult(ok=True, stdout=f"wrote {path}")

    async def read_file(self, path: str) -> CommandResult:
        target = self._resolve_writable(path)
        if target is None or not target.is_file():
            return CommandResult(ok=False, error="not a file in a writable root")
        return CommandResult(ok=True, stdout=target.read_text(encoding="utf-8", errors="replace"))

    async def delete_file(self, path: str) -> CommandResult:
        target = self._resolve_writable(path)
        if target is None:
            return CommandResult(ok=False, error="path not in a writable root")
        if not target.is_file():
            return CommandResult(ok=False, error="not a file in a writable root")
        target.unlink()
        return CommandResult(ok=True, stdout=f"deleted {path}")

    async def reset(self) -> None:
        await self.stop()
        await self.start()

    async def preview_url(self, port: int) -> str | None:
        """A public URL for a service listening on `port` inside this sandbox, if the
        backend supports it. Local/Docker have no public exposure (dev-only); only
        E2B overrides this (App-gen § B6's live-preview requirement, D43)."""
        return None

    async def start_background(self, cmd: str) -> BackgroundHandle:
        """Dispatch ``cmd`` without blocking; only backends with a true remote,
        reconnectable process (currently E2B) support this."""
        raise NotImplementedError(
            "this sandbox backend doesn't support background dispatch — use the e2b backend"
        )

    async def check_background(self, sandbox_id: str, pid: int, job_id: str) -> BackgroundStatus:
        """Reconnect to a previously dispatched background command and report status."""
        raise NotImplementedError(
            "this sandbox backend doesn't support background dispatch — use the e2b backend"
        )


def _run_subprocess_sync(
    cmd: str | list[str],
    *,
    cwd: str,
    timeout: float,
    shell: bool = True,
) -> CommandResult:
    """Run a command synchronously (for use in a thread on Windows)."""
    try:
        result = subprocess.run(
            cmd,
            shell=shell,
            cwd=cwd,
            capture_output=True,
            timeout=timeout,
        )
        return CommandResult(
            ok=(result.returncode == 0),
            stdout=result.stdout.decode(errors="replace"),
            stderr=result.stderr.decode(errors="replace"),
            exit_code=result.returncode,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(ok=False, error=f"timeout after {timeout}s")
    except Exception as exc:
        return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")


class LocalSandbox(Sandbox):
    """Subprocess in the workspace cwd. NO ISOLATION — dev/trusted code only."""

    async def start(self) -> None:
        return None

    async def run_command(self, cmd: str, *, timeout: float | None = None) -> CommandResult:
        t = timeout if timeout is not None else settings.sandbox_timeout
        # WindowsSelectorEventLoop (set by Windows conftest for SSL compat) does not
        # support asyncio subprocess — fall back to thread-pool on win32.
        if sys.platform == "win32":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _run_subprocess_sync(cmd, cwd=str(self.workspace), timeout=t),
            )
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=str(self.workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                out, err = await asyncio.wait_for(proc.communicate(), timeout=t)
            except TimeoutError:
                proc.kill()
                return CommandResult(ok=False, error=f"timeout after {t}s")
            return CommandResult(
                ok=(proc.returncode == 0),
                stdout=out.decode(errors="replace"),
                stderr=err.decode(errors="replace"),
                exit_code=proc.returncode or 0,
            )
        except Exception as exc:
            return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def stop(self) -> None:
        return None


class DockerSandbox(Sandbox):
    """Per-session container: workspace mounted at /workspace, network policy-gated, memory/cpu
    limited, non-root, read-only root fs + noexec tmpfs, all capabilities dropped,
    no-new-privileges, and a pids limit. Container persists across run_command calls
    (files/installs persist); each command runs via `docker exec sh -c 'cd /workspace && <cmd>'`.

    **Until s63 this docstring was a promise, not a description.** It claimed "read-only root fs +
    tmpfs" while `--read-only` and `--tmpfs` appeared zero times in the file -- the root filesystem
    was writable, and no test noticed because none existed. Every flag named above is now actually
    passed and was verified running on base-01, not inferred: root write blocked, /tmp writable but
    noexec, CapEff 0000000000000000, uid 1000, NoNewPrivs 1.

    **The constraint that falls out of --read-only, stated for whoever writes an arrival:** packages
    must be installed into the WORKSPACE (a venv there), never into the image. `pip install` to
    system site-packages fails by design now -- measured, not assumed.
    """

    def __init__(
        self,
        *,
        session_id: str,
        workspace: Path | None = None,
        cred_broker: CredentialBroker | None = None,
        cred_proxy_port: int = 0,
    ) -> None:
        super().__init__(session_id=session_id, workspace=workspace)
        # container name must be filesystem/DNS-safe
        safe = "".join(c if c.isalnum() else "-" for c in session_id)[:40]
        self._name = f"madras-sbx-{safe}"
        self._started = False
        # s46: Credential Brokering — when a broker is supplied, egress is routed through a
        # per-session mitmproxy instance (CredentialProxyServer) instead of the plain
        # none/bridge toggle, so authed calls get their secret injected app-side only.
        self._cred_broker = cred_broker
        self._cred_proxy_port = cred_proxy_port
        self._proxy: CredentialProxyServer | None = None

    async def _docker(self, *args: str, timeout: float = 60.0) -> CommandResult:
        # On Windows use thread-pool to avoid WindowsSelectorEventLoop subprocess limit.
        if sys.platform == "win32":
            return await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _run_subprocess_sync(
                    ["docker", *args], cwd=str(self.workspace), timeout=timeout, shell=False
                ),
            )
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return CommandResult(
                ok=(proc.returncode == 0),
                stdout=out.decode(errors="replace"),
                stderr=err.decode(errors="replace"),
                exit_code=proc.returncode or 0,
            )
        except FileNotFoundError:
            return CommandResult(ok=False, error="docker CLI not found")
        except TimeoutError:
            return CommandResult(ok=False, error="docker command timeout")
        except Exception as exc:
            return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def start(self) -> None:
        if self._started:
            return
        # remove any stale container with the same name
        await self._docker("rm", "-f", self._name)

        extra_args: list[str] = []
        network = "bridge" if self.policy.network_access else "none"
        if self._cred_broker is not None:
            from madras.security.cred_proxy_server import CredentialProxyServer, ca_cert_path

            self._proxy = CredentialProxyServer(
                broker=self._cred_broker, port=self._cred_proxy_port
            )
            await self._proxy.start()
            network = "bridge"  # egress is now gated by the proxy, not the docker network flag
            proxy_url = f"http://host.docker.internal:{self._proxy.port}"
            extra_args += [
                "--add-host",
                "host.docker.internal:host-gateway",
                "-e",
                f"HTTP_PROXY={proxy_url}",
                "-e",
                f"HTTPS_PROXY={proxy_url}",
                "-e",
                "NO_PROXY=localhost,127.0.0.1",
                "-v",
                f"{ca_cert_path()}:/usr/local/share/ca-certificates/madras-ca.crt:ro",
            ]

        run = await self._docker(
            "run",
            "-d",
            "--name",
            self._name,
            "--network",
            network,  # policy-gated egress (or proxy-gated when a cred broker is attached)
            *extra_args,
            "--memory",
            settings.sandbox_memory,
            "--cpus",
            settings.sandbox_cpus,
            # s63: the hardening this class's docstring had CLAIMED since it was written, while
            # passing none of it -- the root filesystem was writable. Measured on base-01 rather
            # than copied from a checklist, because the read-only flag genuinely breaks real work
            # unless the writable paths are right:
            #   plain                       -> pip install works
            #   --read-only                 -> FAILS "No usable temporary directory found"
            #   --read-only + --tmpfs /tmp  -> FAILS, pip falls back to /root/.local (read-only)
            #   ... + venv inside /workspace -> WORKS (requests 2.34.2 installed and imported)
            # So the rule for an arrival is: install into the WORKSPACE, never into the image.
            "--read-only",
            # noexec/nosuid so /tmp is scratch, not a launchpad -- a script written there cannot
            # be executed, which is the usual next step after an injection lands a file.
            "--tmpfs",
            "/tmp:rw,noexec,nosuid",
            # Docker already drops most capabilities by default ("an allowlist instead of a
            # denylist", per its own security docs) -- ALL goes the rest of the way. Verified
            # empty at runtime: CapEff 0000000000000000.
            "--cap-drop",
            "ALL",
            # Closes the setuid escalation path; with --user below, "call sudo, become root" dies.
            # Verified at runtime: NoNewPrivs 1.
            "--security-opt",
            "no-new-privileges",
            # A fork bomb must exhaust its own container, not base-01. 1024, not the CIS example
            # of 100: cgroup pids.max counts THREADS, not processes, so a thread-pooled job blows
            # through a process-shaped number. base-01 has threads-max 673,561 and uses ~452, so
            # ten concurrent sandboxes at this limit is ~1.5% of capacity -- PIDs are not the
            # scarce resource here, memory is. Tighten later from measured pids.current peaks.
            "--pids-limit",
            settings.sandbox_pids_limit,
            "--user",
            "1000:1000",
            "-v",
            f"{self.workspace}:/workspace",
            "-w",
            "/workspace",
            settings.sandbox_image,
            "sleep",
            "infinity",
        )
        if not run.ok:
            raise RuntimeError(f"sandbox start failed: {run.error or run.stderr}")
        if self._cred_broker is not None:
            # trust the broker's CA so MITM'd HTTPS responses verify inside the container
            await self._docker(
                "exec",
                "-u",
                "root",
                self._name,
                "update-ca-certificates",
            )
        self._started = True

    async def run_command(self, cmd: str, *, timeout: float | None = None) -> CommandResult:
        if not self._started:
            await self.start()
        t = timeout if timeout is not None else settings.sandbox_timeout
        res = await self._docker(
            "exec", self._name, "sh", "-c", f"cd /workspace && {cmd}", timeout=t + 5
        )
        return res

    async def stop(self) -> None:
        if self._started:
            await self._docker("rm", "-f", self._name)
            self._started = False
        if self._proxy is not None:
            await self._proxy.stop()
            self._proxy = None


_E2B_WORKSPACE = "/home/user/workspace"  # the remote workspace dir inside the E2B micro-VM


class E2BSandbox(Sandbox):
    """Firecracker micro-VM via E2B's HOSTED cloud API — kept available as an optional backend for
    genuine multi-tenant Firecracker-grade isolation, but NOT the default or production choice
    (D47: self-hosted OSS only, no third-party sandbox SaaS dependency). ``DockerSandbox`` is the
    production self-hosted substrate; this class is a future option if Madras stands up E2B's own
    self-hostable infra (github.com/e2b-dev/infra) or genuinely needs Firecracker-level isolation at
    a scale Docker containers can't provide. Unlike Local/Docker, the workspace lives REMOTELY in
    the sandbox VM, so file ops route to the E2B filesystem (not the host). The e2b SDK is
    synchronous; every call runs in a thread executor to stay non-blocking. ``commands.run`` raises
    ``CommandExitException`` on a non-zero exit — caught + mapped to ``CommandResult(ok=False)`` so
    a failing test never throws.
    """

    def __init__(
        self, *, session_id: str, workspace: Path | None = None, vm_timeout: int = 300
    ) -> None:
        super().__init__(session_id=session_id, workspace=workspace)
        self._vm_timeout = vm_timeout  # the micro-VM's idle lifetime (seconds)
        self._sbx: object | None = None
        self._wd = _E2B_WORKSPACE

    async def _to_thread(self, fn: object, *args: object) -> object:
        return await asyncio.get_event_loop().run_in_executor(None, lambda: fn(*args))  # type: ignore[operator]

    def _remote(self, path: str) -> str | None:
        """Resolve ``path`` under the remote workspace; None if it escapes (no host FS touched)."""
        import posixpath

        full = posixpath.normpath(posixpath.join(self._wd, path))
        if full != self._wd and not full.startswith(self._wd + "/"):
            return None
        return full

    async def start(self) -> None:
        if self._sbx is not None:
            return
        import os

        from e2b_code_interpreter import Sandbox as _E2B  # type: ignore[reportMissingTypeStubs]

        os.environ.setdefault("E2B_API_KEY", settings.e2b_api_key)

        def _create() -> object:
            s = _E2B.create(timeout=self._vm_timeout)
            s.commands.run(f"mkdir -p {self._wd}")
            return s

        self._sbx = await self._to_thread(_create)

    async def run_command(self, cmd: str, *, timeout: float | None = None) -> CommandResult:
        if self._sbx is None:
            await self.start()
        t = timeout if timeout is not None else settings.sandbox_timeout

        def _run() -> CommandResult:
            from e2b.sandbox.commands.command_handle import (  # type: ignore[reportMissingTypeStubs]
                CommandExitException,
            )

            try:
                _r = self._sbx.commands.run(cmd, cwd=self._wd, timeout=int(t) + 5)  # type: ignore[attr-defined]
                r = cast("Any", _r)
                return CommandResult(
                    ok=(r.exit_code == 0),
                    stdout=r.stdout or "",
                    stderr=r.stderr or "",
                    exit_code=r.exit_code,
                )
            except CommandExitException as _exc:
                exc: Any = _exc
                return CommandResult(
                    ok=False,
                    stdout=exc.stdout or "",
                    stderr=exc.stderr or "",
                    exit_code=exc.exit_code,
                )
            except Exception as exc:
                return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")

        return cast("CommandResult", await self._to_thread(_run))

    async def write_file(self, path: str, content: str) -> CommandResult:
        if self._sbx is None:
            await self.start()
        remote = self._remote(path)
        if remote is None:
            return CommandResult(ok=False, error="path escapes workspace")
        try:
            await self._to_thread(self._sbx.files.write, remote, content)  # type: ignore[attr-defined]
            return CommandResult(ok=True, stdout=f"wrote {path}")
        except Exception as exc:
            return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def read_file(self, path: str) -> CommandResult:
        if self._sbx is None:
            await self.start()
        remote = self._remote(path)
        if remote is None:
            return CommandResult(ok=False, error="path escapes workspace")
        try:
            data = await self._to_thread(self._sbx.files.read, remote)  # type: ignore[attr-defined]
            return CommandResult(ok=True, stdout=data if isinstance(data, str) else str(data))
        except Exception as exc:
            return CommandResult(ok=False, error=f"not a file in workspace: {exc}")

    async def delete_file(self, path: str) -> CommandResult:
        remote = self._remote(path)
        if remote is None or self._sbx is None:
            return CommandResult(ok=False, error="path escapes workspace")
        try:
            await self._to_thread(self._sbx.files.remove, remote)  # type: ignore[attr-defined]
            return CommandResult(ok=True, stdout=f"deleted {path}")
        except Exception as exc:
            return CommandResult(ok=False, error=f"{type(exc).__name__}: {exc}")

    async def stop(self) -> None:
        if self._sbx is not None:
            sbx = self._sbx
            self._sbx = None
            await self._to_thread(sbx.kill)  # type: ignore[attr-defined]

    async def preview_url(self, port: int) -> str | None:
        if self._sbx is None:
            await self.start()
        try:
            host = await self._to_thread(self._sbx.get_host, port)  # type: ignore[attr-defined]
            return f"https://{host}"
        except Exception:
            return None

    async def start_background(self, cmd: str) -> BackgroundHandle:
        """Dispatch ``cmd`` without blocking, redirecting output to job-marker files in the
        remote workspace rather than relying on the process handle for output — a fresh
        reconnect (a new session, a new process) only needs ``sandbox_id``+``job_id`` to find
        them, which stay valid regardless of who dispatched the job."""
        if self._sbx is None:
            await self.start()
        job_id = uuid.uuid4().hex[:12]
        jobs_dir = f"{self._wd}/.jobs"
        out, err, exitf = (
            f"{jobs_dir}/{job_id}.out",
            f"{jobs_dir}/{job_id}.err",
            f"{jobs_dir}/{job_id}.exit",
        )
        wrapped = f"mkdir -p {jobs_dir} && ({cmd}) > {out} 2> {err}; echo $? > {exitf}"

        def _dispatch() -> BackgroundHandle:
            _handle = self._sbx.commands.run(wrapped, cwd=self._wd, background=True)  # type: ignore[attr-defined]
            handle = cast("Any", _handle)
            _sbx_id = self._sbx.sandbox_id  # type: ignore[attr-defined]
            sbx_id = cast("Any", _sbx_id)
            return BackgroundHandle(
                sandbox_id=sbx_id,
                pid=handle.pid,
                job_id=job_id,
            )

        return await self._to_thread(_dispatch)  # type: ignore[return-value]

    async def check_background(self, sandbox_id: str, pid: int, job_id: str) -> BackgroundStatus:
        """Reconnect to ``sandbox_id`` (valid from any process) and check ``job_id``'s
        marker files: the exit-code file only appears once the wrapped command finishes,
        so its presence — not the live process list — is the source of truth for "done"."""
        import os

        from e2b_code_interpreter import Sandbox as _E2B  # type: ignore[reportMissingTypeStubs]

        os.environ.setdefault("E2B_API_KEY", settings.e2b_api_key)
        jobs_dir = f"{self._wd}/.jobs"
        out, err, exitf = (
            f"{jobs_dir}/{job_id}.out",
            f"{jobs_dir}/{job_id}.err",
            f"{jobs_dir}/{job_id}.exit",
        )

        def _check() -> BackgroundStatus:
            try:
                sbx = _E2B.connect(sandbox_id)
            except Exception as exc:
                return BackgroundStatus(running=False, error=f"sandbox unreachable: {exc}")
            try:
                exit_raw = sbx.files.read(exitf)
            except Exception:
                # No exit marker yet — still running (or pid vanished without writing one).
                still_running = any(p.pid == pid for p in sbx.commands.list())
                return BackgroundStatus(running=still_running)
            try:
                exit_code = int(str(exit_raw).strip())
            except ValueError:
                return BackgroundStatus(running=False, error=f"malformed exit marker: {exit_raw!r}")
            try:
                stdout = sbx.files.read(out)
            except Exception:
                stdout = ""
            try:
                stderr = sbx.files.read(err)
            except Exception:
                stderr = ""
            return BackgroundStatus(
                running=False,
                ok=(exit_code == 0),
                exit_code=exit_code,
                stdout=str(stdout),
                stderr=str(stderr),
            )

        return await self._to_thread(_check)  # type: ignore[return-value]


def build_sandbox(
    *,
    session_id: str,
    backend: str | None = None,
    workspace: Path | None = None,
) -> Sandbox:
    """Factory: picks the sandbox backend from config (or override).

    local (dev/trusted) -> docker (single-host isolation, self-hosted — the production choice,
    D47) -> e2b (hosted multi-tenant micro-VM; kept available, not the default — see
    ``E2BSandbox``'s own docstring). See the module docstring's scaling ladder.
    """
    b = backend or settings.sandbox_backend
    if b == "e2b":
        return E2BSandbox(session_id=session_id, workspace=workspace)
    if b == "docker":
        return DockerSandbox(session_id=session_id, workspace=workspace)
    return LocalSandbox(session_id=session_id, workspace=workspace)
