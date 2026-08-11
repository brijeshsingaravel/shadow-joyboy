"""ETDI-style cryptographic signing for MCP servers (ASI04 — authenticity, not just integrity).

The 2026 frontier (ETDI / signed provenance attestation): ``manifest_hash`` + ``verify_pin``
prove a server's tool definitions HAVEN'T DRIFTED (rug-pull), but not that they're AUTHENTIC —
a forged-but-self-consistent server passes pinning. ETDI binds an IMMUTABLE, VERSIONED tool
manifest to a publisher IDENTITY via a signature: we sign (publisher, version, manifest_hash)
with Ed25519 and verify on connect against a trusted publisher key. Pure crypto, deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from madras.mcp.security import manifest_hash


def _payload(publisher: str, version: str, mhash: str) -> bytes:
    """Canonical signed bytes — binds identity + version + manifest so the signature is
    immutable per (publisher, version, manifest)."""
    return f"etdi/v1\n{publisher}\n{version}\n{mhash}".encode()


def generate_keypair() -> tuple[str, str]:
    """(private_hex, public_hex) Ed25519 keypair for a publisher."""
    sk = Ed25519PrivateKey.generate()
    return sk.private_bytes_raw().hex(), sk.public_key().public_bytes_raw().hex()


def load_private(priv_hex: str) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(bytes.fromhex(priv_hex))


@dataclass
class SignedManifest:
    publisher: str
    version: str
    manifest_hash: str
    signature: str  # hex Ed25519 signature over _payload(...)


@dataclass
class SignatureVerdict:
    ok: bool
    reason: str  # authentic | drifted | untrusted | forged | malformed
    publisher: str = ""


def sign_manifest(
    *, publisher: str, version: str, tools: list[dict[str, Any]], private_key: Ed25519PrivateKey
) -> SignedManifest:
    """Publisher signs its tool manifest → a detached SignedManifest (the ETDI attestation)."""
    mhash = manifest_hash(tools)
    sig = private_key.sign(_payload(publisher, version, mhash))
    return SignedManifest(
        publisher=publisher, version=version, manifest_hash=mhash, signature=sig.hex()
    )


def verify_signed_manifest(
    signed: SignedManifest,
    *,
    current_tools: list[dict[str, Any]],
    trusted_keys: dict[str, str],
) -> SignatureVerdict:
    """Verify a signed manifest against the LIVE tools + a trusted publisher→pubkey map.

    Verdicts: ``drifted`` (live hash != signed hash → rug-pull/tamper), ``untrusted``
    (publisher not in the trusted set), ``forged`` (signature fails → impersonation),
    ``malformed`` (bad key/sig encoding), ``authentic`` (trusted publisher AND hash matches)."""
    cur = manifest_hash(current_tools)
    if cur != signed.manifest_hash:
        return SignatureVerdict(ok=False, reason="drifted", publisher=signed.publisher)
    pub_hex = trusted_keys.get(signed.publisher)
    if not pub_hex:
        return SignatureVerdict(ok=False, reason="untrusted", publisher=signed.publisher)
    try:
        pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
        pub.verify(
            bytes.fromhex(signed.signature),
            _payload(signed.publisher, signed.version, signed.manifest_hash),
        )
    except InvalidSignature:
        return SignatureVerdict(ok=False, reason="forged", publisher=signed.publisher)
    except ValueError:
        return SignatureVerdict(ok=False, reason="malformed", publisher=signed.publisher)
    return SignatureVerdict(ok=True, reason="authentic", publisher=signed.publisher)
