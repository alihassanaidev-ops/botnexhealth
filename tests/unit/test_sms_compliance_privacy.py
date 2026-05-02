from __future__ import annotations

import pytest

from src.app.config import settings
from src.app.models.sms_consent import ConsentChannel, ConsentRecord, ConsentStatus, SmsSuppression
from src.app.models.sms_history_log import SmsHistoryLog, SmsStatus
from src.app.services.sms_compliance import SmsComplianceService, SmsSendBlockedError
from src.app.services.sms_privacy import (
    CASL_FOOTER,
    hash_phone,
    mask_phone,
    prepare_outbound_sms_body,
    redact_payload,
    sanitize_provider_error,
)
from src.app.services.sms_service import SmsService


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return self.value


class _ExecuteResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _ScalarResult(self.value)

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.added = []

    async def execute(self, *_args, **_kwargs):
        return _ExecuteResult(self.results.pop(0) if self.results else None)

    def add(self, row):
        self.added.append(row)

    async def flush(self):
        return None


def test_sms_phone_mask_and_hash_are_phi_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")

    assert mask_phone("+1 (212) 555-1234") == "+*******1234"
    assert hash_phone("+12125551234") == hash_phone("+1 212 555 1234")
    assert hash_phone("+12125551234") == hash_phone("(212) 555-1234")
    assert hash_phone("+12125551234") == hash_phone("212-555-1234")


def test_provider_error_sanitization_redacts_phone_and_email() -> None:
    sanitized = sanitize_provider_error(
        "Failed for +1 (212) 555-1234 and jane@example.com"
    )

    assert "212" not in sanitized
    assert "jane@example.com" not in sanitized
    assert "[phone-redacted]" in sanitized
    assert "[email-redacted]" in sanitized


def test_redact_payload_removes_sms_body_and_masks_numbers() -> None:
    redacted = redact_payload({"To": "+12125551234", "Body": "Patient message", "MessageSid": "SM123"})

    assert redacted["To"] == "[redacted]"
    assert redacted["Body"] == "[redacted]"
    assert redacted["MessageSid"] == "SM123"


def test_redact_payload_retell_payload_only_keeps_allowlisted_identifiers() -> None:
    redacted = redact_payload(
        {
            "transcript": "Patient John Smith DOB 1980-04-27 called about treatment.",
            "call_analysis": {"summary": "John Smith needs help", "duration_ms": 1000},
            "recording_url": "https://retell.example/raw.wav",
            "call_sid": "CA123",
        }
    )

    assert redacted == {
        "transcript": "[redacted]",
        "call_analysis": "[redacted]",
        "recording_url": "[redacted]",
        "call_sid": "CA123",
    }


def test_prepare_outbound_sms_body_adds_identity_and_casl_footer() -> None:
    prepared = prepare_outbound_sms_body(body="Your appointment is confirmed.", clinic_identity="Downtown Clinic")

    assert prepared.startswith("Downtown Clinic:")
    assert CASL_FOOTER in prepared


@pytest.mark.asyncio
async def test_sms_compliance_allows_when_no_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    service = SmsComplianceService(_FakeSession(None, None, None))  # DNC, suppression, consent

    identity = await service.assert_can_send(
        institution_id="inst",
        location_id="loc",
        to_number="+12125551234",
    )

    assert identity.phone_masked == "+*******1234"


@pytest.mark.asyncio
async def test_sms_compliance_blocks_active_suppression(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    suppression = SmsSuppression(
        institution_id="inst",
        channel=ConsentChannel.SMS.value,
        phone_hash=hash_phone("+12125551234") or "",
        phone_masked="+*******1234",
        is_active=True,
        source="manual",
    )
    service = SmsComplianceService(_FakeSession(None, suppression))

    with pytest.raises(SmsSendBlockedError):
        await service.assert_can_send(
            institution_id="inst",
            location_id="loc",
            to_number="+12125551234",
        )


@pytest.mark.asyncio
async def test_sms_compliance_blocks_latest_revoked_consent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    consent = ConsentRecord(
        institution_id="inst",
        channel=ConsentChannel.SMS.value,
        phone_hash=hash_phone("+12125551234") or "",
        phone_masked="+*******1234",
        status=ConsentStatus.REVOKED.value,
        source="manual",
    )
    service = SmsComplianceService(_FakeSession(None, None, consent))

    with pytest.raises(SmsSendBlockedError):
        await service.assert_can_send(
            institution_id="inst",
            location_id="loc",
            to_number="+12125551234",
        )


@pytest.mark.asyncio
async def test_release_suppression_does_not_grant_consent_when_no_rows_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "encryption_key", "legacy-secret-value-1234567890")
    session = _FakeSession([])
    service = SmsComplianceService(session)

    released = await service.release_suppression(
        institution_id="inst",
        location_id="loc",
        phone="+12125551234",
        grant_consent=True,
    )

    assert released == 0
    assert session.added == []


@pytest.mark.asyncio
async def test_sms_status_callback_is_only_source_of_delivered() -> None:
    row = SmsHistoryLog(
        from_number="+15550000000",
        institution_location_id="11111111-1111-1111-1111-111111111111",
        status=SmsStatus.SENT.value,
        message_sid="SM123",
    )
    service = SmsService(_FakeSession(row))

    updated = await service.update_delivery_status(
        message_sid="SM123",
        provider_status="delivered",
    )

    assert updated is row
    assert row.status == SmsStatus.DELIVERED.value
    assert row.provider_status == "delivered"
