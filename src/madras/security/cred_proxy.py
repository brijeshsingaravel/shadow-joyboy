"""Credential Brokering's transport — a mitmproxy addon that puts `CredentialBroker.forward()`
on the actual egress path of a sandboxed agent. `CredentialBroker` itself stays pure/injectable
(row 82, tested in isolation); this module is the thin, real-HTTP glue: intercept the sandbox's
outbound request, ask the broker for the upstream headers, inject or block.

Regular (explicit) mitmproxy mode: the sandbox is configured with HTTP_PROXY/HTTPS_PROXY pointing
here. HTTPS is MITM'd with mitmproxy's own generated CA — the sandbox container trusts that CA
(installed at container start), so the secret injection also works for TLS traffic.
"""

from __future__ import annotations

from typing import Any

from madras.security.cred_broker import CredentialBroker


class CredentialBrokerAddon:
    """mitmproxy addon: `request(flow)` is called for every intercepted request. The sandbox
    never sends a real secret — only the broker (app-side, running this addon) resolves and
    injects one for a matching, egress-allowed domain."""

    def __init__(self, broker: CredentialBroker) -> None:
        self.broker = broker

    def request(self, flow: Any) -> None:
        sandbox_headers = {k: v for k, v in flow.request.headers.items()}
        result = self.broker.forward(flow.request.pretty_url, sandbox_headers)
        if result.blocked:
            from mitmproxy import http

            flow.response = http.Response.make(
                403, f"egress blocked: {result.reason}".encode(), {"Content-Type": "text/plain"}
            )
            return
        for header, value in result.upstream_headers.items():
            flow.request.headers[header] = value
