"""SMS block reasons must be stable identifiers, not free-text."""

from __future__ import annotations


from src.app.services.sms_compliance import SmsBlockedReason, SmsSendBlockedError


def test_block_reasons_are_stable_identifiers():
    """No PII-shaped strings, no spaces, no free text."""
    for value in (
        SmsBlockedReason.DO_NOT_CONTACT,
        SmsBlockedReason.OPTED_OUT,
        SmsBlockedReason.CONSENT_REVOKED,
    ):
        assert " " not in value
        assert value.islower()


def test_blocked_error_carries_reason_attribute():
    err = SmsSendBlockedError(SmsBlockedReason.OPTED_OUT)
    assert err.reason == SmsBlockedReason.OPTED_OUT
    # The string form is also the reason — safe to log directly.
    assert str(err) == SmsBlockedReason.OPTED_OUT
