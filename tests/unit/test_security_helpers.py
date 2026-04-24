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
