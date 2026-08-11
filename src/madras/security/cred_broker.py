"""Credential brokering — authed sandbox egress, the secret never enters the sandbox (row 82).

A sandboxed agent often needs an authed API (GitHub, Stripe, ...). Putting the token IN the sandbox
env means a compromised sandbox can exfiltrate it. Instead the secret stays APP-SIDE: the sandbox's
outbound request (carrying NO secret) is forwarded through this broker, which injects the auth
header for the MATCHING domain only and sends it upstream. Even a fully-compromised sandbox can't
leak the credential — it never has it. Domain-scoped + egress-checked (`NetPolicy`) + JIT-resolved
(ASI03, task-scoped) + audited WITHOUT logging the secret. Fail-closed. Pure/injectable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from madras.security.net_policy import NetPolicy


@dataclass
class BrokeredCredential:
    domain: str  # suffix-matched host, e.g. "api.github.com"
    header: str  # e.g. "Authorization"
    resolver: Callable[[], str]  # JIT task-scoped secret resolver (ASI03)
    scheme: str = "Bearer {secret}"  # template; {secret} substituted at inject time


@dataclass
class BrokerResult:
    upstream_headers: dict[str, str]  # what the BROKER sends upstream (app-side; holds secret)
    injected: bool = False
    domain: str = ""
    blocked: bool = False
    reason: str = ""


def _host(url: str) -> str:
    parsed = urlparse(url if "://" in url else "https://" + url)
    return (parsed.hostname or "").lower()


def _host_matches(host: str, domain: str) -> bool:
    d = domain.lower().lstrip(".")
    return host == d or host.endswith("." + d)


@dataclass
class CredentialBroker:
    net_policy: NetPolicy = field(default_factory=NetPolicy)
    audit: Callable[[dict[str, Any]], None] | None = None
    _creds: list[BrokeredCredential] = field(default_factory=list[BrokeredCredential], init=False)

    def _audit(self, event: str, **kw: Any) -> None:
        if self.audit is not None:
            self.audit({"event": f"cred_broker_{event}", **kw})  # never includes the secret value

    def register(
        self,
        domain: str,
        header: str,
        *,
        resolver: Callable[[], str],
        scheme: str = "Bearer {secret}",
    ) -> None:
        self._creds.append(BrokeredCredential(domain, header, resolver, scheme))

    def forward(self, url: str, headers: dict[str, str] | None = None) -> BrokerResult:
        """Broker a sandbox's outbound request. `headers` is what the SANDBOX sent (must carry NO
        secret). Returns the headers the broker forwards UPSTREAM (app-side), with the auth header
        injected only for a matching, egress-allowed domain. The input dict is never mutated."""
        sandbox_headers = dict(headers or {})
        verdict = self.net_policy.check(url)
        if not verdict.allow:
            self._audit("blocked", url=url, reason=verdict.reason)
            return BrokerResult(sandbox_headers, blocked=True, reason=verdict.reason)

        host = _host(url)
        for cred in self._creds:
            if _host_matches(host, cred.domain):
                secret = cred.resolver()  # JIT, task-scoped (ASI03)
                upstream = dict(sandbox_headers)
                upstream[cred.header] = cred.scheme.format(secret=secret)
                self._audit("injected", domain=cred.domain, header=cred.header, host=host)
                return BrokerResult(upstream, injected=True, domain=cred.domain)

        self._audit("passthrough", host=host)
        return BrokerResult(sandbox_headers, injected=False)
