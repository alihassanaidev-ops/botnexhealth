from __future__ import annotations

import base64

import pytest

from src.app.config import settings
from src.app.models.institution import _get_encryption_key, decrypt_value, encrypt_value


def test_encrypt_value_supports_base64_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = base64.urlsafe_b64encode(b"0123456789abcdef0123456789abcdef").decode("ascii")
    monkeypatch.setattr(settings, "encryption_key", key)

    encrypted = encrypt_value("secret-value")

    assert encrypted is not None
    assert decrypt_value(encrypted) == "secret-value"


def test_encrypt_value_supports_legacy_random_string_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        settings,
        "encryption_key",
        "legacy-secret-generated-by-cdk-without-base64-format-abcdef123456",
    )

    encrypted = encrypt_value("another-secret")

    assert encrypted is not None
    assert decrypt_value(encrypted) == "another-secret"


def test_cdk_generated_unpadded_base64_key_decodes_to_aes_256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # CDK generates 43 alphanumeric chars. That is valid unpadded base64 and
    # decodes to exactly 32 bytes once padding is restored by the app.
    monkeypatch.setattr(settings, "encryption_key", "A" * 43)

    assert len(_get_encryption_key()) == 32
