"""Unit test for the SMS late-price backfill (Plan 11).

Twilio's first terminal status ("sent") often has Price=null; a later "delivered"
carries the real Price. Both hit the same idempotency key — without backfill the
price was dropped. record() must fill a NULL cost/segments on the existing row.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.exc import IntegrityError

from src.app.services.usage_metering_service import UsageMeteringService


class _NestedCM:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def _session(existing):
    session = AsyncMock()
    session.add = MagicMock()
    # flush raises IntegrityError → duplicate key → backfill path
    session.flush = AsyncMock(side_effect=IntegrityError("s", {}, Exception("dup")))
    session.begin_nested = MagicMock(return_value=_NestedCM())
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session.execute = AsyncMock(return_value=result)
    return session


def _record(session, **over):
    kw = dict(
        institution_id="inst-1",
        channel="sms",
        direction="outbound",
        provider="twilio",
        idempotency_key="sms:SM123",
        currency="USD",
    )
    kw.update(over)
    return asyncio.run(UsageMeteringService(session).record(**kw))


def test_backfills_null_cost_on_duplicate():
    existing = SimpleNamespace(cost_amount=None, currency="USD", segments=None)
    result = _record(_session(existing), cost_amount=Decimal("0.00750"), segments=2)
    assert result is existing
    assert existing.cost_amount == Decimal("0.00750")
    assert existing.segments == 2


def test_does_not_overwrite_existing_cost():
    existing = SimpleNamespace(cost_amount=Decimal("0.01000"), currency="USD", segments=3)
    _record(_session(existing), cost_amount=Decimal("0.00750"), segments=9)
    # already had a value → never clobbered
    assert existing.cost_amount == Decimal("0.01000")
    assert existing.segments == 3


def test_duplicate_with_no_new_data_is_noop():
    existing = SimpleNamespace(cost_amount=None, currency="USD", segments=None)
    result = _record(_session(existing), cost_amount=None, segments=None)
    assert result is None  # nothing to backfill


def test_backfills_segments_only_when_cost_absent():
    existing = SimpleNamespace(cost_amount=Decimal("0.005"), currency="USD", segments=None)
    _record(_session(existing), cost_amount=None, segments=4)
    assert existing.segments == 4
    assert existing.cost_amount == Decimal("0.005")  # untouched
