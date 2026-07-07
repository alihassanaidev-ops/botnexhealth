"""Unit tests for the staff DNC admin route + release service (Plan 12)."""

from __future__ import annotations

import asyncio
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
    return SimpleNamespace(institution_id=institution_id, id=user_id, role="INSTITUTION_ADMIN")


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

    body = DncCreateRequest(phone="+14165551234", scope="institution", reason="asked at front desk")
    written_row = SimpleNamespace(
        phone_masked="***1234", scope="institution", source="manual", reason="asked at front desk",
        location_id=None, contact_id=None, created_at=MagicMock(),
    )
    mock_compliance = MagicMock()
    mock_compliance.set_do_not_contact = AsyncMock(return_value=written_row)

    with patch.object(mod, "get_db_session", return_value=_cm_session()), \
         patch.object(mod, "SmsComplianceService", return_value=mock_compliance), \
         patch.object(mod, "log_audit", new=AsyncMock()) as mock_audit:
        result = asyncio.run(add_do_not_contact(MagicMock(), body, _admin()))

    assert result.scope == "institution"
    assert result.phone_masked == "***1234"
    mock_compliance.set_do_not_contact.assert_awaited_once()
    kwargs = mock_compliance.set_do_not_contact.call_args.kwargs
    assert str(kwargs["created_by_user_id"]) == "u-1"
    mock_audit.assert_awaited_once()  # audited


def test_remove_reports_released_flag():
    from src.app.api.routes import do_not_contact as mod
    from src.app.api.routes.do_not_contact import DncReleaseRequest, remove_do_not_contact

    mock_compliance = MagicMock()
    mock_compliance.release_do_not_contact = AsyncMock(return_value=SimpleNamespace())  # found

    with patch.object(mod, "get_db_session", return_value=_cm_session()), \
         patch.object(mod, "SmsComplianceService", return_value=mock_compliance), \
         patch.object(mod, "log_audit", new=AsyncMock()):
        out = asyncio.run(
            remove_do_not_contact(MagicMock(), DncReleaseRequest(phone="+14165551234"), _admin())
        )
    assert out == {"released": True}
