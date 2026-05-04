from __future__ import annotations

import pytest

from src.app.config import settings
from src.app.security import get_client_ip, keyed_hash


def test_keyed_hash_is_stable_and_purpose_scoped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")

    first = keyed_hash("5551234567", purpose="phone-lookup-hash-v1")
    second = keyed_hash("5551234567", purpose="phone-lookup-hash-v1")
    other = keyed_hash("5551234567", purpose="retell-log-hash-v1")

    assert first == second
    assert first != other


def test_get_client_ip_only_trusts_forwarded_header_for_trusted_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")

    trusted_ip = get_client_ip(
        forwarded_for="198.51.100.10, 10.0.1.15",
        direct_host="10.0.1.15",
    )
    untrusted_ip = get_client_ip(
        forwarded_for="198.51.100.10",
        direct_host="203.0.113.50",
    )

    assert trusted_ip == "198.51.100.10"
    assert untrusted_ip == "203.0.113.50"


def test_get_client_ip_rejects_client_spoofed_leftmost_xff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client that prepends a fake IP to X-Forwarded-For before the
    request hits the proxy chain must not be able to control the value
    we record. The real client's IP is the rightmost untrusted entry,
    not the leftmost.
    """
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")

    # Attacker sets ``X-Forwarded-For: 1.2.3.4`` on their own request.
    # CloudFront/ALB don't strip that header; they append their own
    # observation. The resulting chain seen by FastAPI looks like:
    #     <spoofed>, <real_client>, <CloudFront edge>
    # The ASGI direct peer is the ALB, in 10.0.0.0/8.
    spoofed_chain = "1.2.3.4, 198.51.100.10, 10.0.1.15"

    resolved = get_client_ip(
        forwarded_for=spoofed_chain,
        direct_host="10.0.1.15",
    )

    assert resolved == "198.51.100.10", (
        "Took the leftmost entry, which is fully attacker-controlled. The "
        "real client is the rightmost entry that is NOT a trusted proxy."
    )
    assert resolved != "1.2.3.4"


def test_get_client_ip_falls_back_to_leftmost_when_chain_is_fully_trusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An internal-only chain (every hop is in trusted_proxy_networks)
    has no client to point at — return the leftmost entry, which is the
    earliest hop we have on record."""
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")

    resolved = get_client_ip(
        forwarded_for="10.0.1.5, 10.0.2.10",
        direct_host="10.0.2.10",
    )

    assert resolved == "10.0.1.5"


def test_get_client_ip_skips_invalid_xff_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Garbage tokens (e.g., ``unknown``) must not produce ``unknown`` as
    the resolved IP — they're skipped and the rightmost-untrusted rule
    still applies."""
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")

    resolved = get_client_ip(
        forwarded_for="unknown, 198.51.100.10, 10.0.1.5",
        direct_host="10.0.1.5",
    )

    assert resolved == "198.51.100.10"


def test_get_client_ip_returns_direct_host_when_xff_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "trusted_proxy_cidrs", "10.0.0.0/8")

    resolved = get_client_ip(forwarded_for=None, direct_host="10.0.1.5")

    assert resolved == "10.0.1.5"
