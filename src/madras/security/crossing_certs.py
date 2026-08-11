"""Short-lived mTLS identities for the crossing transport (Phase P slice 3c, RFC-0002 §5.9).

§5.9 requires "**every hop authenticated** (device↔edge↔cloud, no unauthenticated hop, consistent
with §5.1's 'gated' law applied to the network itself)". `crossing_grpc` provides the transport;
this provides the identities that make it authenticated rather than merely encrypted.

**Why not step-ca, and what would change when it arrives.** The s56 radar chose step-ca (Adopt),
and its reason is worth stating precisely because it is not "we need certificates":

- step-ca's value is *distributing and rotating* trust ACROSS MACHINES -- short-lived certificates,
  renewed automatically, which is ASI03's JIT-credential doctrine applied to the network;
- proving mTLS works needs a CA, a server identity and a client identity, which `cryptography`
  (already a dependency) produces.

There is currently exactly one machine: the receiver is not deployed. Cross-machine trust
distribution is therefore not yet a problem, and standing up a CA daemon to solve it would be
infrastructure ahead of need. `issue_chain()` is the seam step-ca replaces on the day `base-01`
actually runs a receiver -- which is also the day rotation starts to matter.

**The ephemeral property is NOT deferred, because it shapes callers.** Certificates default to a
600-second lifetime, matching ASI03's `max_ttl_seconds` for tool credentials. Code written against
long-lived certificates would have to be unpicked when step-ca arrives; code written against
10-minute ones does not. A caller that holds a chain for an hour will find it expired, which is the
intended lesson.

**Keys never touch disk.** They exist for the life of the process and are passed to gRPC as PEM
bytes. A private key written to disk is a private key in a backup, and this project's backups are
now hourly.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import grpc
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DEFAULT_TTL_SECONDS = 600
"""Matches ASI03's `max_ttl_seconds` for tool credentials -- the same JIT doctrine, applied to the
network instead of to a tool call."""


@dataclass(frozen=True)
class CertChain:
    """A CA and the two identities that authenticate one crossing hop.

    Both ends are issued together because a chain is only meaningful as a set: a server certificate
    without the CA that signed it cannot be verified, and a client certificate from a different CA
    is exactly the case `test_a_client_from_a_different_ca_is_rejected` pins.
    """

    ca_cert_pem: bytes
    server_cert_pem: bytes
    server_key_pem: bytes
    client_cert_pem: bytes
    client_key_pem: bytes
    client_not_before: dt.datetime
    client_not_after: dt.datetime


def _key() -> ec.EllipticCurvePrivateKey:
    # P-256: what TLS 1.3 stacks are fastest at, and what step-ca issues by default -- so the
    # swap to step-ca does not also change the key type under callers.
    return ec.generate_private_key(ec.SECP256R1())


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _pem_cert(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def _pem_key(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def issue_chain(*, ttl_seconds: int = DEFAULT_TTL_SECONDS, host: str = "127.0.0.1") -> CertChain:
    """Mint a CA plus a server and client identity, all valid for `ttl_seconds`.

    The CA is generated per call and never persisted: there is no long-lived root to protect,
    because there is nothing yet that needs to trust the same root twice. That changes the day two
    machines must agree on an authority -- which is precisely when step-ca replaces this function.
    """
    now = dt.datetime.now(dt.UTC)
    not_before = now - dt.timedelta(seconds=30)  # tolerate small clock skew between ends
    not_after = now + dt.timedelta(seconds=ttl_seconds)

    ca_key = _key()
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(_name("madras-crossing-ca"))
        .issuer_name(_name("madras-crossing-ca"))
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    def _leaf(cn: str, *, server: bool) -> tuple[bytes, bytes]:
        key = _key()
        builder = (
            x509.CertificateBuilder()
            .subject_name(_name(cn))
            .issuer_name(ca_cert.subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(not_before)
            .not_valid_after(not_after)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(
                x509.ExtendedKeyUsage(
                    [ExtendedKeyUsageOID.SERVER_AUTH if server else ExtendedKeyUsageOID.CLIENT_AUTH]
                ),
                critical=False,
            )
        )
        if server:
            # Without a SAN the handshake fails on hostname verification -- modern TLS stacks
            # ignore the Common Name entirely.
            builder = builder.add_extension(
                x509.SubjectAlternativeName([x509.DNSName(host), x509.DNSName("localhost")]),
                critical=False,
            )
        return _pem_cert(builder.sign(ca_key, hashes.SHA256())), _pem_key(key)

    server_cert, server_key = _leaf(host, server=True)
    client_cert, client_key = _leaf("madras-device", server=False)

    return CertChain(
        ca_cert_pem=_pem_cert(ca_cert),
        server_cert_pem=server_cert,
        server_key_pem=server_key,
        client_cert_pem=client_cert,
        client_key_pem=client_key,
        client_not_before=not_before,
        client_not_after=not_after,
    )


def server_credentials(chain: CertChain) -> grpc.ServerCredentials:
    """Server-side credentials that REQUIRE a client certificate.

    `require_client_auth=True` is the whole point. Without it this is ordinary TLS -- encrypted,
    and happy to serve anyone -- which satisfies "private" while failing §5.9's actual requirement
    that every hop be *authenticated*. Encryption without authentication is the failure that looks
    most like success.
    """
    return grpc.ssl_server_credentials(
        [(chain.server_key_pem, chain.server_cert_pem)],
        root_certificates=chain.ca_cert_pem,
        require_client_auth=True,
    )


def client_credentials(chain: CertChain) -> grpc.ChannelCredentials:
    """Client-side credentials presenting this chain's client identity."""
    return grpc.ssl_channel_credentials(
        root_certificates=chain.ca_cert_pem,
        private_key=chain.client_key_pem,
        certificate_chain=chain.client_cert_pem,
    )


__all__ = [
    "DEFAULT_TTL_SECONDS",
    "CertChain",
    "client_credentials",
    "issue_chain",
    "server_credentials",
]
