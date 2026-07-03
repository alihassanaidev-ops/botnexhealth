"""Unit tests for usage-metering ingestion (Plan 11 core)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from src.app.models.usage_event import UsageEvent
from src.app.services.usage_metering_service import (
    UsageMeteringService,
    parse_cost_amount,
    parse_segments,
)


class _NestedCM:
    """Minimal async context manager standing in for session.begin_nested()."""

    def __init__(self, *, raise_integrity: bool = False):
        self._raise_integrity = raise_integrity

    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _make_session(*, integrity_error: bool = False) -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    if integrity_error:
        session.flush = AsyncMock(
            side_effect=IntegrityError("stmt", {}, Exception("dup"))
        )
    else:
        session.flush = AsyncMock()
    session.begin_nested = MagicMock(return_value=_NestedCM())
    return session


def test_record_inserts_usage_event() -> None:
    session = _make_session()
    svc = UsageMeteringService(session)
    event = asyncio.run(
        svc.record(
            institution_id="inst-1",
            channel="sms",
            direction="outbound",
            provider="twilio",
            segments=2,
            cost_amount=Decimal("0.00750"),
            idempotency_key="sms:SM123",
        )
    )
    assert event is not None
    assert isinstance(event, UsageEvent)
    assert event.institution_id == "inst-1"
    assert event.segments == 2
    assert event.idempotency_key == "sms:SM123"
    session.add.assert_called_once()


def test_record_is_idempotent_no_op_on_duplicate_key() -> None:
    """A second call with the same key hits the unique index -> no-op (None)."""
    session = _make_session(integrity_error=True)
    svc = UsageMeteringService(session)
    result = asyncio.run(
        svc.record(
            institution_id="inst-1",
            channel="sms",
            direction="outbound",
            provider="twilio",
            idempotency_key="sms:SM123",
        )
    )
    assert result is None


def test_record_email_records_one_email() -> None:
    session = _make_session()
    svc = UsageMeteringService(session)
    event = asyncio.run(
        svc.record(
            institution_id="inst-1",
            location_id="loc-1",
            channel="email",
            direction="outbound",
            provider="resend",
            emails=1,
            provider_message_id="resend-abc",
            idempotency_key="email:resend-abc",
        )
    )
    assert event is not None
    assert event.channel == "email"
    assert event.emails == 1
    assert event.provider == "resend"
    assert event.location_id == "loc-1"


def test_parse_segments_extracts_int() -> None:
    assert parse_segments("3") == 3
    assert parse_segments(" 2 ") == 2
    assert parse_segments(None) is None
    assert parse_segments("") is None
    assert parse_segments("abc") is None


def test_parse_cost_amount_normalizes_twilio_price() -> None:
    # Twilio reports Price as a negative string; usage cost stored positive.
    assert parse_cost_amount("-0.00750") == Decimal("0.00750")
    assert parse_cost_amount("0.01000") == Decimal("0.01000")
    assert parse_cost_amount(None) is None
    assert parse_cost_amount("") is None
    assert parse_cost_amount("not-a-number") is None


def test_sms_status_ingestion_extracts_segments_and_price() -> None:
    """The Twilio status webhook feeds NumSegments/Price into a usage event."""
    session = _make_session()
    svc = UsageMeteringService(session)
    # Simulate the fields the webhook parses from the Twilio callback form.
    num_segments = parse_segments("2")
    price = parse_cost_amount("-0.01500")
    event = asyncio.run(
        svc.record(
            institution_id="inst-1",
            location_id="loc-1",
            channel="sms",
            direction="outbound",
            provider="twilio",
            segments=num_segments,
            cost_amount=price,
            currency="USD",
            provider_message_id="SM999",
            idempotency_key="sms:SM999",
        )
    )
    assert event is not None
    assert event.segments == 2
    assert event.cost_amount == Decimal("0.01500")
    assert event.provider_message_id == "SM999"
    assert event.currency == "USD"
