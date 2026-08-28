"""Unit tests for the staff DNC admin route + release service (Plan 12)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from src.app.api.rate_limit import limiter
from src.app.services.sms_compliance import SmsComplianceService


@pytest.fixture(autouse=True)
def _no_rate_limit():
    """Call the route handlers directly without slowapi's real-Request check."""
    prev = limiter.enabled
    limiter.enabled = False
    yield
    limiter.enabled = prev


# ── release_do_not_contact service ───────────────────────────────────────────


def _svc_session(existing):
    session = AsyncMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.first.return_value = existing
    session.execute = AsyncMock(return_value=result)
    return session


def test_release_deactivates_active_row():
    row = SimpleNamespace(is_active=True, released_by_user_id=None, released_at=None)
    session = _svc_session(row)
    released = asyncio.run(
        SmsComplianceService(session).release_do_not_contact(
            institution_id="inst-1", phone="+14165551234", released_by_user_id="u-1"
        )
    )
    assert released is row
    assert row.is_active is False
    assert row.released_by_user_id == "u-1"
    assert row.released_at is not None
    session.flush.assert_awaited()


def test_release_is_noop_when_no_active_dnc():
    session = _svc_session(None)
    released = asyncio.run(
        SmsComplianceService(session).release_do_not_contact(
            institution_id="inst-1", phone="+14165551234"
        )
    )
    assert released is None


# ── route validation ─────────────────────────────────────────────────────────


def _admin(institution_id="inst-1", user_id="u-1"):
    return SimpleNamespace(
        institution_id=institution_id, id=user_id, role="INSTITUTION_ADMIN"
    )


def test_add_requires_institution():
    from src.app.api.routes.do_not_contact import DncCreateRequest, add_do_not_contact

    body = DncCreateRequest(phone="+14165551234", scope="institution")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_do_not_contact(MagicMock(), body, _admin(institution_id=None)))
    assert exc.value.status_code == 400


def test_add_location_scope_requires_location_id():
    from src.app.api.routes.do_not_contact import DncCreateRequest, add_do_not_contact

    body = DncCreateRequest(phone="+14165551234", scope="location", location_id=None)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(add_do_not_contact(MagicMock(), body, _admin()))
    assert exc.value.status_code == 400
    assert "location_id" in exc.value.detail


def _cm_session():
    s = AsyncMock()
    s.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=s)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_add_writes_dnc_and_audits():
    from src.app.api.routes import do_not_contact as mod
    from src.app.api.routes.do_not_contact import DncCreateRequest, add_do_not_contact

    body = DncCreateRequest(
        phone="+14165551234", scope="institution", reason="asked at front desk"
    )
    written_row = SimpleNamespace(
        phone_masked="***1234",
        scope="institution",
        source="manual",
        reason="asked at front desk",
        location_id=None,
        contact_id=None,
        created_at=MagicMock(),
    )
    mock_compliance = MagicMock()
    mock_compliance.set_do_not_contact = AsyncMock(return_value=written_row)

    with (
        patch.object(mod, "get_db_session", return_value=_cm_session()),
        patch.object(mod, "SmsComplianceService", return_value=mock_compliance),
        patch.object(mod, "log_audit", new=AsyncMock()) as mock_audit,
    ):
        result = asyncio.run(add_do_not_contact(MagicMock(), body, _admin()))

    assert result.scope == "institution"
    assert result.phone_masked == "***1234"
    mock_compliance.set_do_not_contact.assert_awaited_once()
    kwargs = mock_compliance.set_do_not_contact.call_args.kwargs
    assert str(kwargs["created_by_user_id"]) == "u-1"
    mock_audit.assert_awaited_once()  # audited


def test_remove_reports_released_flag():
    from src.app.api.routes import do_not_contact as mod
    from src.app.api.routes.do_not_contact import (
        DncReleaseRequest,
        remove_do_not_contact,
    )

    mock_compliance = MagicMock()
    mock_compliance.release_do_not_contact = AsyncMock(
        return_value=SimpleNamespace(id="dnc-1", location_id=None)
    )  # found

    with (
        patch.object(mod, "get_db_session", return_value=_cm_session()),
        patch.object(mod, "SmsComplianceService", return_value=mock_compliance),
        patch.object(mod, "log_audit", new=AsyncMock()),
    ):
        out = asyncio.run(
            remove_do_not_contact(
                MagicMock(), DncReleaseRequest(phone="+14165551234"), _admin()
            )
        )
    assert out == {"released": True}


def _result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    return result


def test_list_groups_sms_and_voice_tags_for_same_patient():
    from src.app.api.routes.do_not_contact import _list_patient_records

    now = datetime.now(timezone.utc)
    sms = SimpleNamespace(
        id="sms-1",
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        channel="sms",
        phone_hash="phone-hash",
        phone_masked="***1234",
        is_active=True,
        source="twilio_keyword",
        reason="STOP",
        created_at=now,
    )
    voice = SimpleNamespace(
        id="voice-1",
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        channel="voice",
        phone_hash="phone-hash",
        phone_masked="***1234",
        email_hash=None,
        email_masked=None,
        status="revoked",
        source="system",
        reason="voice_spoken_optout",
        created_at=now,
    )
    contact = SimpleNamespace(
        id="contact-1",
        phone_hash="phone-hash",
        full_name="Jane Patient",
        first_name="Jane",
        last_name="Patient",
    )
    session = AsyncMock()
    session.execute = AsyncMock(
        side_effect=[_result([sms]), _result([voice]), _result([]), _result([contact])]
    )

    records = asyncio.run(_list_patient_records(session, "inst-1"))

    assert len(records) == 1
    assert records[0].patient_name == "Jane Patient"
    assert {entry.channel for entry in records[0].channels} == {"sms", "voice"}
    assert {entry.record_type for entry in records[0].channels} == {
        "sms_suppression",
        "consent_record",
    }


def test_effective_revocations_respect_newer_global_grant():
    from src.app.api.routes.do_not_contact import _effective_revoked_consents

    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 1, 2, tzinfo=timezone.utc)
    location_revoke = SimpleNamespace(
        id="1",
        channel="voice",
        phone_hash="phone-hash",
        email_hash=None,
        location_id="loc-1",
        status="revoked",
        created_at=old,
    )
    global_grant = SimpleNamespace(
        id="2",
        channel="voice",
        phone_hash="phone-hash",
        email_hash=None,
        location_id=None,
        status="granted",
        created_at=new,
    )

    assert _effective_revoked_consents([global_grant, location_revoke]) == []


def test_release_sms_tag_deactivates_only_that_suppression_and_grants_sms():
    from src.app.api.routes import do_not_contact as mod

    row = SimpleNamespace(
        id="sms-1",
        institution_id="inst-1",
        location_id="loc-1",
        contact_id="contact-1",
        phone_hash="phone-hash",
        phone_masked="***1234",
        is_active=True,
        released_by_user_id=None,
        released_at=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    compliance = MagicMock()
    compliance.record_consent_identity = AsyncMock()

    with patch.object(mod, "SmsComplianceService", return_value=compliance):
        released, channel, location_id = asyncio.run(
            mod._release_sms_suppression(session, "inst-1", "sms-1", "user-1")
        )

    assert (released, channel, location_id) == (True, "sms", "loc-1")
    assert row.is_active is False
    kwargs = compliance.record_consent_identity.call_args.kwargs
    assert kwargs["channel"].value == "sms"
    assert kwargs["status"].value == "granted"
