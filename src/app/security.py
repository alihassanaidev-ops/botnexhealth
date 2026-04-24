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
    """Return the original client IP only when the immediate peer is trusted."""
    if forwarded_for and _is_trusted_proxy(direct_host):
        original_ip = _first_forwarded_ip(forwarded_for)
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

    return any(client_ip in network for network in settings.trusted_proxy_networks)


def _first_forwarded_ip(forwarded_for: str) -> str | None:
    for candidate in forwarded_for.split(","):
        candidate = candidate.strip()
        if not candidate:
            continue
        try:
            ipaddress.ip_address(candidate)
        except ValueError:
            continue
        return candidate
    return None
