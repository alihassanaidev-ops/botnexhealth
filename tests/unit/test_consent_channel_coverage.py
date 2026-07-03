"""Static coverage: ConsentChannel enum values and model check constraints.

Catches regressions where the enum or constraint is narrowed back to SMS-only.
"""
from __future__ import annotations


def test_consent_channel_has_all_three_values() -> None:
    from src.app.models.sms_consent import ConsentChannel

    assert ConsentChannel.SMS.value == "sms"
    assert ConsentChannel.EMAIL.value == "email"
    assert ConsentChannel.VOICE.value == "voice"
    assert set(ConsentChannel) == {ConsentChannel.SMS, ConsentChannel.EMAIL, ConsentChannel.VOICE}


def test_consent_record_channel_constraint_includes_all_channels() -> None:
    from sqlalchemy import CheckConstraint
    from src.app.models.sms_consent import ConsentRecord

    constraints = {
        c.name: c
        for c in ConsentRecord.__table_args__
        if isinstance(c, CheckConstraint)
    }
    ck = constraints["ck_consent_records_channel"]
    expr = str(ck.sqltext)
    assert "'email'" in expr or "email" in expr
    assert "'voice'" in expr or "voice" in expr
    assert "'sms'" in expr or "sms" in expr


def test_sms_suppression_channel_constraint_includes_all_channels() -> None:
    from sqlalchemy import CheckConstraint
    from src.app.models.sms_consent import SmsSuppression

    constraints = {
        c.name: c
        for c in SmsSuppression.__table_args__
        if isinstance(c, CheckConstraint)
    }
    ck = constraints["ck_sms_suppressions_channel"]
    expr = str(ck.sqltext)
    assert "'email'" in expr or "email" in expr
    assert "'voice'" in expr or "voice" in expr
    assert "'sms'" in expr or "sms" in expr
