"""Network egress policy — a first-class allow/deny layer over outbound requests (ASI05 egress;
the OpenClaw net-policy pattern).

Every outbound URL (web_fetch, browser, MCP HTTP, sandbox egress) is checked here BEFORE the
request: scheme allowlist (https by default), domain allow/deny lists, and an **SSRF / private-IP
block** (loopback, RFC-1918, link-local, reserved, and the cloud metadata IP 169.254.169.254).
Deny always wins; an allowlist, when set, restricts to it. Pure + deterministic — DNS isn't
required for the policy (IP-literal + name-pattern blocks); a resolver can be layered for full
SSRF coverage of hostnames.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from ipaddress import ip_address
from urllib.parse import urlparse

_VALID_HOST = re.compile(r"^[a-z0-9.\-:]+$")  # hostname or IP literal (ipv6 brackets stripped)
_DEFAULT_SCHEMES = frozenset({"https"})
_PRIVATE_HOSTS = frozenset(
    {
        "localhost",
        "ip6-localhost",
        "metadata.google.internal",
        "metadata",
    }
)
_METADATA_IP = "169.254.169.254"


@dataclass
class EgressVerdict:
    allow: bool
    reason: str = ""


@dataclass
class NetPolicy:
    allow_schemes: frozenset[str] = _DEFAULT_SCHEMES
    deny_domains: tuple[str, ...] = ()
    allow_domains: tuple[str, ...] | None = None  # if set, ONLY these (suffix) are allowed
    block_private: bool = True

    def check(self, url: str) -> EgressVerdict:
        parsed = urlparse(url if "://" in url else "https://" + url)
        scheme = (parsed.scheme or "").lower()
        host = (parsed.hostname or "").lower()
        if not host or not _VALID_HOST.match(host):
            return EgressVerdict(False, "no valid host in URL")
        if scheme not in self.allow_schemes:
            return EgressVerdict(False, f"scheme '{scheme}' not allowed")
        if self.block_private and self._is_private(host):
            return EgressVerdict(False, f"private/loopback/metadata host '{host}' blocked (SSRF)")
        if any(self._host_matches(host, d) for d in self.deny_domains):
            return EgressVerdict(False, f"domain '{host}' on deny list")
        if self.allow_domains is not None and not any(
            self._host_matches(host, d) for d in self.allow_domains
        ):
            return EgressVerdict(False, f"domain '{host}' not on allow list")
        return EgressVerdict(True, "ok")

    @staticmethod
    def _is_private(host: str) -> bool:
        if host in _PRIVATE_HOSTS or host.endswith((".local", ".internal")):
            return True
        try:
            ip = ip_address(host)
        except ValueError:
            return False  # a hostname (not an IP literal) — name-pattern block above
        return bool(
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_unspecified
            or str(ip) == _METADATA_IP
        )

    @staticmethod
    def _host_matches(host: str, domain: str) -> bool:
        domain = domain.lower().lstrip(".")
        return host == domain or host.endswith("." + domain)
