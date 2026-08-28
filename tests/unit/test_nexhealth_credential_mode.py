"""The NexHealth credential mode is an explicit choice, not an inference.

Before this, "which NexHealth account does this clinic authenticate as?" was
derived from whether `institutions.nexhealth_api_key_encrypted` happened to be
populated, and anything missing fell back to the shared platform key. That is a
silent failure mode: a clinic configured for its own credential keeps working on
the platform account after a bad save, a rotated encryption key, or a partial
admin update, so nobody notices. It also consumes the wrong account's per-key
rate limit, and because NexHealth ties webhook endpoint ownership to the
authenticating key, a silent switch can orphan that clinic's subscriptions.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.dependencies import (
    INSTITUTION_CREDENTIAL_MODE,
    PLATFORM_CREDENTIAL_MODE,
    NexHealthCredentialError,
    resolve_nexhealth_credential,
)


def _institution(mode: str | None, *, key: str | None = None, decrypt_raises: bool = False):
    class _Inst:
        id = "inst-1"
        nexhealth_credential_mode = mode
        nexhealth_api_key_encrypted = "cipher" if (key or decrypt_raises) else None

        @property
        def nexhealth_api_key(self):
            if decrypt_raises:
                raise ValueError("decrypt failed")
            return key

    return _Inst()


@pytest.fixture(autouse=True)
def platform_key(monkeypatch):
    from src.app.config import settings

    monkeypatch.setattr(settings, "nexhealth_api_key", "platform-key", raising=False)


def test_own_key_mode_uses_the_institution_key():
    cred = resolve_nexhealth_credential(_institution(INSTITUTION_CREDENTIAL_MODE, key="clinic-key"))
    assert cred.mode == INSTITUTION_CREDENTIAL_MODE
    assert cred.api_key == "clinic-key"


def test_own_key_mode_refuses_to_fall_back_when_the_key_is_missing():
    """The regression this whole change exists to prevent."""
    with pytest.raises(NexHealthCredentialError, match="Refusing to fall back"):
        resolve_nexhealth_credential(_institution(INSTITUTION_CREDENTIAL_MODE))


def test_own_key_mode_refuses_when_the_key_cannot_be_decrypted():
    """A rotated encryption key must not silently become platform access."""
    with pytest.raises(NexHealthCredentialError, match="Refusing to fall back"):
        resolve_nexhealth_credential(_institution(INSTITUTION_CREDENTIAL_MODE, decrypt_raises=True))


def test_platform_mode_ignores_a_stored_institution_key():
    """Mode is authoritative in both directions.

    A key left behind from a previous configuration must not quietly take
    effect — switching to platform mode has to actually mean platform.
    """
    cred = resolve_nexhealth_credential(
        _institution(PLATFORM_CREDENTIAL_MODE, key="stale-clinic-key")
    )
    assert cred.mode == PLATFORM_CREDENTIAL_MODE
    assert cred.api_key == "platform-key"


def test_missing_mode_defaults_to_platform():
    """Rows predating the column, and the no-institution case."""
    cred = resolve_nexhealth_credential(_institution(None))
    assert cred.mode == PLATFORM_CREDENTIAL_MODE
    assert resolve_nexhealth_credential(None).mode == PLATFORM_CREDENTIAL_MODE


def test_unrecognised_mode_is_rejected():
    with pytest.raises(NexHealthCredentialError, match="unrecognised"):
        resolve_nexhealth_credential(_institution("byo"))


def test_platform_mode_still_errors_when_no_platform_key_configured(monkeypatch):
    from src.app.config import settings

    monkeypatch.setattr(settings, "nexhealth_api_key", None, raising=False)
    with pytest.raises(NexHealthCredentialError, match="not configured for the platform"):
        resolve_nexhealth_credential(_institution(PLATFORM_CREDENTIAL_MODE))


def test_each_mode_gets_its_own_rate_limit_bucket():
    """The hash keys the token cache and rate limiter, so it must differ."""
    own = resolve_nexhealth_credential(_institution(INSTITUTION_CREDENTIAL_MODE, key="clinic-key"))
    plat = resolve_nexhealth_credential(_institution(PLATFORM_CREDENTIAL_MODE))
    assert own.api_key_hash != plat.api_key_hash


# --- the write path: a config the resolver would refuse must not be storable ---


class _FakeSession:
    async def flush(self):  # pragma: no cover - trivial
        ...

    async def refresh(self, _obj):  # pragma: no cover - trivial
        ...


def _service():
    from src.app.services.institution_service import InstitutionService

    return InstitutionService(_FakeSession())


class _Row:
    """Minimal institution row for the update path."""

    slug = "clinic"
    nexhealth_api_key_encrypted = None
    nexhealth_credential_mode = "platform"

    def __init__(self, *, stored_key: bool = False):
        if stored_key:
            self.nexhealth_api_key_encrypted = "cipher"


@pytest.mark.asyncio
async def test_cannot_switch_to_institution_mode_without_a_key():
    """Reject at the write, not on the next NexHealth call."""
    with pytest.raises(ValueError, match="without a NexHealth API key"):
        await _service().update(_Row(), nexhealth_credential_mode="institution")


@pytest.mark.asyncio
async def test_can_switch_to_institution_mode_with_a_key_in_the_same_request():
    row = _Row()
    await _service().update(
        row, nexhealth_credential_mode="institution", nexhealth_api_key="clinic-key"
    )
    assert row.nexhealth_credential_mode == "institution"


@pytest.mark.asyncio
async def test_can_switch_to_institution_mode_when_a_key_is_already_stored():
    row = _Row(stored_key=True)
    await _service().update(row, nexhealth_credential_mode="institution")
    assert row.nexhealth_credential_mode == "institution"


@pytest.mark.asyncio
async def test_switching_back_to_platform_needs_no_key():
    row = _Row(stored_key=True)
    await _service().update(row, nexhealth_credential_mode="platform")
    assert row.nexhealth_credential_mode == "platform"


@pytest.mark.asyncio
async def test_unknown_mode_is_rejected_at_the_write():
    with pytest.raises(ValueError, match="must be one of"):
        await _service().update(_Row(), nexhealth_credential_mode="byo")
