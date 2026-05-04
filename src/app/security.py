"""Shared security helpers for hashing, secret derivation, and proxy handling."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from src.app.config import settings


def derive_secret_key(
    *,
    purpose: str,
    secret: str | None = None,
    length: int = 32,
) -> bytes:
    """Derive a fixed-length key from configured secret material."""
    secret_material = secret or settings.encryption_key or settings.jwt_secret
    if not secret_material:
        raise RuntimeError("Secret material is not configured")

    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=length,
        salt=None,
        info=purpose.encode("utf-8"),
    )
    return hkdf.derive(secret_material.encode("utf-8"))


def keyed_hash(
    value: str,
    *,
    purpose: str,
    truncate_hex: int | None = None,
    secret: str | None = None,
) -> str:
    """Generate a purpose-scoped HMAC-SHA256 digest."""
    key = derive_secret_key(purpose=f"hash:{purpose}", secret=secret, length=32)
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest[:truncate_hex] if truncate_hex else digest


def get_client_ip(*, forwarded_for: str | None, direct_host: str | None) -> str | None:
    """Return the original client IP only when the immediate peer is trusted.

    Walks ``X-Forwarded-For`` from right to left, skipping any IP that is
    in the configured trusted-proxy networks. The first untrusted IP we
    hit is the real client; if every entry is trusted (a fully-internal
    chain) we fall back to the leftmost entry.

    The previous implementation took the leftmost valid IP, which is
    spoofable: a client can prepend any value to ``X-Forwarded-For``
    before the request reaches any proxy, and proxies append their own
    observation without removing user input. That meant once the
    immediate peer was a trusted proxy (e.g. AWS ALB), the leftmost
    entry — fully attacker-controlled — became the rate-limiter key
    and the audit-log forensic IP.
    """
    if forwarded_for and _is_trusted_proxy(direct_host):
        original_ip = _untrusted_ip_from_right(forwarded_for)
        if original_ip:
            return original_ip
    return direct_host


def _is_trusted_proxy(host: str | None) -> bool:
    if not host or not settings.trusted_proxy_networks:
        return False

    try:
        client_ip = ipaddress.ip_address(host)
    except ValueError:
        return False

    return _ip_in_trusted_networks(client_ip)


def _ip_in_trusted_networks(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> bool:
    return any(ip in network for network in settings.trusted_proxy_networks)


def _untrusted_ip_from_right(forwarded_for: str) -> str | None:
    """Pick the rightmost entry that is NOT in a trusted-proxy network.

    Walking right-to-left strips off the (trusted) proxy hops the request
    passed through; the first non-trusted address we encounter is the
    closest the chain can attest to the real client. If the entire chain
    is trusted (internal-only deployment), return the leftmost entry —
    that's the earliest hop on record.
    """
    valid: list[str] = []
    for candidate in forwarded_for.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        valid.append(candidate)

    if not valid:
        return None

    for candidate in reversed(valid):
        if not _ip_in_trusted_networks(ipaddress.ip_address(candidate)):
            return candidate

    return valid[0]
