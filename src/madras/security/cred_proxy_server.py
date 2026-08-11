"""Runs the CredentialBrokerAddon as a real local mitmproxy instance — one per DockerSandbox
session, started in `DockerSandbox.start()` and torn down in `stop()`. Localhost-bound only;
the sandbox container reaches it via `host.docker.internal`.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from madras.security.cred_broker import CredentialBroker
from madras.security.cred_proxy import CredentialBrokerAddon


def ca_cert_path() -> Path:
    """Path to mitmproxy's own CA cert (generated on first run under ~/.mitmproxy)."""
    return Path.home() / ".mitmproxy" / "mitmproxy-ca-cert.pem"


class CredentialProxyServer:
    """Owns one mitmproxy `DumpMaster` bound to `127.0.0.1:port`."""

    def __init__(self, *, broker: CredentialBroker, port: int) -> None:
        self.broker = broker
        self.port = port
        self._master: object | None = None
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._master is not None:
            return
        from mitmproxy import options
        from mitmproxy.tools.dump import DumpMaster

        opts = options.Options(listen_host="127.0.0.1", listen_port=self.port)
        master = DumpMaster(opts, with_termlog=False, with_dumper=False)
        # mitmproxy ships no type stubs; DumpMaster.addons.add is untyped third-party API.
        master.addons.add(CredentialBrokerAddon(self.broker))  # pyright: ignore[reportUnknownMemberType]
        self._master = master
        self._task = asyncio.ensure_future(master.run())
        # give the listener a moment to bind before the caller starts the sandbox container
        await asyncio.sleep(0.2)

    async def stop(self) -> None:
        if self._master is None:
            return
        self._master.shutdown()  # type: ignore[attr-defined]
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                pass
        self._master = None
        self._task = None
