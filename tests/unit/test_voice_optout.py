"""Unit tests for V-2 spoken opt-out → voice suppression (detection gate + write)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.sms_consent import ConsentSource, DncScope
from src.app.services.automation import voice_optout_service
from src.app.services.automation.voice_optout_service import (
    detect_voice_optout,
    suppress_voice_optout,
)


# ── Detection gate (do-not-guess) ────────────────────────────────────────────


def test_detection_off_when_key_unset():
    with patch.object(voice_optout_service.settings, "retell_optout_analysis_key", None):
        assert detect_voice_optout({"patient_opted_out": True}) is False


def test_detection_true_when_configured_key_truthy():
    with patch.object(voice_optout_service.settings, "retell_optout_analysis_key", "patient_opted_out"):
        assert detect_voice_optout({"patient_opted_out": True}) is True
        assert detect_voice_optout({"patient_opted_out": "yes"}) is True
        assert detect_voice_optout({"patient_opted_out": 1}) is True


def test_detection_false_when_configured_key_falsy_or_missing():
    with patch.object(voice_optout_service.settings, "retell_optout_analysis_key", "patient_opted_out"):
        assert detect_voice_optout({"patient_opted_out": False}) is False
        assert detect_voice_optout({"patient_opted_out": "no"}) is False
        assert detect_voice_optout({}) is False
        assert detect_voice_optout(None) is False


# ── Write path ───────────────────────────────────────────────────────────────


def _session_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_suppress_writes_location_scoped_dnc():
    session = AsyncMock()
    session.commit = AsyncMock()
    captured = {}

    async def _set_dnc(**kwargs):
        captured.update(kwargs)
        return MagicMock()

    mock_compliance = MagicMock()
    mock_compliance.set_do_not_contact = AsyncMock(side_effect=_set_dnc)

    with patch(
        "src.app.services.automation.voice_optout_service.get_system_db_session",
        return_value=_session_cm(session),
    ), patch(
        "src.app.services.automation.voice_optout_service.SmsComplianceService",
        return_value=mock_compliance,
    ):
        ok = asyncio.run(
            suppress_voice_optout(
                institution_id="inst-1",
                location_id="loc-1",
                contact_id="c-1",
                phone="+14165551234",
                call_id="call-1",
            )
        )

    assert ok is True
    assert captured["scope"] == DncScope.LOCATION
    assert captured["source"] == ConsentSource.SYSTEM
    assert captured["location_id"] == "loc-1"
    assert captured["contact_id"] == "c-1"
    assert captured["phone"] == "+14165551234"
    session.commit.assert_awaited_once()


def test_suppress_noops_without_phone():
    ok = asyncio.run(
        suppress_voice_optout(
            institution_id="inst-1", location_id="loc-1", contact_id="c-1", phone=None
        )
    )
    assert ok is False


def test_suppress_failopen_on_error():
    with patch(
        "src.app.services.automation.voice_optout_service.get_system_db_session",
        side_effect=RuntimeError("db down"),
    ):
        ok = asyncio.run(
            suppress_voice_optout(
                institution_id="inst-1", location_id="loc-1", contact_id="c-1", phone="+14165551234"
            )
        )
    assert ok is False  # never raises
