from __future__ import annotations

import pytest

from src.app.config import Settings
from src.app.nexhealth.api_contract import (
    NexHealthAPIContract,
    normalize_nexhealth_api_contract,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v2", NexHealthAPIContract.LEGACY_V2),
        ("v2.2.2", NexHealthAPIContract.LEGACY_V2),
        ("legacy_v2", NexHealthAPIContract.LEGACY_V2),
        ("v3", NexHealthAPIContract.STABLE_V3),
        ("v3.0.0", NexHealthAPIContract.STABLE_V3),
        ("v20240412", NexHealthAPIContract.STABLE_V3),
        ("stable_v3", NexHealthAPIContract.STABLE_V3),
    ],
)
def test_normalize_nexhealth_api_contract_aliases(
    raw: str,
    expected: NexHealthAPIContract,
) -> None:
    assert normalize_nexhealth_api_contract(raw) is expected


def test_settings_reject_unknown_nexhealth_api_version() -> None:
    with pytest.raises(ValueError, match="Unsupported NexHealth API version"):
        Settings(jwt_secret="test", app_env="test", nexhealth_api_version="latest")


def test_settings_derives_legacy_v2_headers_from_contract() -> None:
    settings = Settings(jwt_secret="test", app_env="test", nexhealth_api_version="v2.2.2")

    assert settings.nexhealth_api_contract is NexHealthAPIContract.LEGACY_V2
    assert settings.api_version == "v2"
    assert settings.accept_header == "application/vnd.Nexhealth+json;version=2"
    assert settings.nexhealth_api_version == "v2"
    assert settings.nexhealth_accept == "application/vnd.Nexhealth+json;version=2"


def test_settings_derives_stable_v3_headers_from_contract() -> None:
    settings = Settings(
        jwt_secret="test",
        app_env="test",
        nexhealth_api_version="stable_v3",
        nexhealth_accept="application/vnd.Nexhealth+json;version=2",
    )

    assert settings.nexhealth_api_contract is NexHealthAPIContract.STABLE_V3
    assert settings.api_version == "v3.0.0"
    assert settings.accept_header == "application/json"
    assert settings.nexhealth_api_version == "v3.0.0"
    assert settings.nexhealth_accept == "application/json"


def test_contract_request_headers_do_not_send_legacy_accept_for_v3() -> None:
    headers = NexHealthAPIContract.STABLE_V3.request_headers(
        authorization="Bearer test-token"
    )

    assert headers == {
        "Accept": "application/json",
        "Authorization": "Bearer test-token",
        "Nex-Api-Version": "v3.0.0",
    }
