"""Item 15 · a message that never arrived must not count as a contact.

Twilio reports twice: once on acceptance, once on the real outcome. Only the
first ever reached the campaign, so a message the carrier dropped was recorded
as a delivered contact.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.delivery_receipt_service import (
    apply_sms_delivery_receipt,
    classify_receipt,
)


class TestClassifyReceipt:
    @pytest.mark.parametrize("status", ["delivered", "DELIVERED", " delivered "])
    def test_delivered(self, status: str) -> None:
        assert classify_receipt(status) == "delivered"

    @pytest.mark.parametrize("status", ["failed", "undelivered", "UNDELIVERED"])
    def test_hard_failures(self, status: str) -> None:
        assert classify_receipt(status) == "undelivered"

    @pytest.mark.parametrize("status", ["queued", "sent", "sending", "accepted", ""])
    def test_in_flight_statuses_say_nothing_new(self, status: str) -> None:
        """Non-terminal statuses must not overwrite a real outcome."""
        assert classify_receipt(status) is None


def _apply(step, provider_status, provider_error=None):
    """Run the receipt against a mocked institution-scoped session."""
    session = AsyncMock()
    session.execute = AsyncMock(
        return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=step))
    )
    session.commit = AsyncMock()

    class _Ctx:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    with patch(
        "src.app.services.automation.delivery_receipt_service.get_system_db_session",
        return_value=_Ctx(),
    ):
        outcome = asyncio.run(
            apply_sms_delivery_receipt(
                institution_id="inst-1",
                workflow_run_id="run-1",
                message_sid="SM123",
                provider_status=provider_status,
                provider_error=provider_error,
            )
        )
    return outcome, session


def _step():
    return MagicMock(result_metadata={"message_sid": "SM123"}, result_code="sent")


def test_undelivered_is_recorded_against_the_step():
    step = _step()
    outcome, session = _apply(step, "undelivered", provider_error="30003")
    assert outcome == "undelivered"
    assert step.result_code == "sent:undelivered"
    assert step.result_metadata["delivery_status"] == "undelivered"
    assert step.result_metadata["delivery_error"] == "30003"
    assert "delivery_reported_at" in step.result_metadata
    session.commit.assert_awaited_once()


def test_delivered_is_distinguishable_from_merely_accepted():
    step = _step()
    outcome, _ = _apply(step, "delivered")
    assert outcome == "delivered"
    assert step.result_code == "sent:delivered"
    assert "delivery_error" not in step.result_metadata


def test_in_flight_receipt_does_not_touch_the_step():
    step = _step()
    outcome, session = _apply(step, "sent")
    assert outcome is None
    assert step.result_code == "sent", "a non-terminal status must not overwrite"
    session.commit.assert_not_awaited()


def test_message_sid_is_preserved_so_later_receipts_still_match():
    step = _step()
    _apply(step, "delivered")
    assert step.result_metadata["message_sid"] == "SM123"


def test_receipt_for_a_non_campaign_message_is_ignored():
    """Agent-sent and reply messages carry a run id but no send step."""
    outcome, session = _apply(None, "delivered")
    assert outcome is None
    session.commit.assert_not_awaited()


def test_a_failed_receipt_does_not_fail_the_twilio_callback():
    """The SMS row and the usage event are committed before this runs.

    Raising here would return 500 to Twilio, which retries the callback and
    redoes work that already succeeded. A receipt we could not record is worth
    a log line, not a failed webhook.
    """
    import inspect

    import src.app.api.routes.twilio_webhooks as hooks

    source = inspect.getsource(hooks.sms_status)
    call = source.index("apply_sms_delivery_receipt")
    before = source[:call]
    # the call sits inside a try, and the handler swallows what it raises
    assert before.rstrip().endswith("try:") or "try:" in before.split("if row.workflow_run_id")[-1]
    assert "except Exception" in source[call:]
    assert "logger.exception" in source[call:]
