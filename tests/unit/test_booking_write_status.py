"""Item 4 · a booking that has not reached the practice must not report success.

The GoTracker Cloud Service queues a write until the clinic's machine is
reachable, so an accepted booking may sit for hours and may never land at all.
Before this, the mapper defaulted a statusless response to "scheduled" and
attached fixed text claiming the booking succeeded - and the Retell handler
returns that straight to the voice agent, which tells the patient.
"""

from __future__ import annotations

import pytest

from src.app.pms.gotracker import mappers as gt
from src.app.pms.models import BookingWriteStatus
from src.app.pms.nexhealth import mappers as nh


def _gt(payload: dict, *, success: bool = True):
    return gt.to_booking_result({"data": payload}, success=success)


class TestGoTrackerPendingWrites:
    def test_absent_write_status_is_pending_not_scheduled(self) -> None:
        """The regression this item exists to close."""
        result = _gt({"appointment_id": "123", "start_time": "2026-09-01T14:00:00"})
        assert result.write_status == BookingWriteStatus.PENDING.value
        assert result.status != "scheduled"
        assert result.status == "pending"

    def test_pending_message_does_not_claim_success(self) -> None:
        result = _gt({"appointment_id": "123"})
        assert "successfully" not in result.message.lower()
        assert result.message == gt.PENDING_WRITE_MESSAGE

    def test_explicit_confirmed_write_reports_success(self) -> None:
        result = _gt({"appointment_id": "123", "write_status": "confirmed"})
        assert result.write_status == BookingWriteStatus.CONFIRMED.value
        assert result.message == gt.CONFIRMED_WRITE_MESSAGE

    @pytest.mark.parametrize("raw", ["written", "COMPLETE", "Completed", "confirmed"])
    def test_confirmed_synonyms_normalise(self, raw: str) -> None:
        assert _gt({"write_status": raw}).write_status == BookingWriteStatus.CONFIRMED.value

    @pytest.mark.parametrize("raw", ["pending", "waiting", "QUEUED", "accepted"])
    def test_pending_synonyms_normalise(self, raw: str) -> None:
        assert _gt({"write_status": raw}).write_status == BookingWriteStatus.PENDING.value

    def test_pascal_case_key_is_honoured(self) -> None:
        """The Cloud Service mixes snake and Pascal casing elsewhere."""
        assert _gt({"WriteStatus": "confirmed"}).write_status == BookingWriteStatus.CONFIRMED.value

    def test_unrecognised_write_status_is_unknown_not_confirmed(self) -> None:
        """Fail safe: an unfamiliar value must never read as confirmed."""
        assert _gt({"write_status": "banana"}).write_status == BookingWriteStatus.UNKNOWN.value

    def test_explicit_practice_status_still_wins(self) -> None:
        """A real status from the practice software is better information."""
        result = _gt({"status": "cancelled", "write_status": "confirmed"})
        assert result.status == "cancelled"

    def test_failure_is_unchanged(self) -> None:
        result = _gt({}, success=False)
        assert result.success is False
        assert result.status == "error"
        assert result.message == ""


class TestNexHealthWritesAreImmediate:
    def test_successful_booking_is_confirmed(self) -> None:
        result = nh.to_booking_result({"data": {"appt": {"id": 9}}}, success=True)
        assert result.write_status == BookingWriteStatus.CONFIRMED.value
        assert result.status == "confirmed"

    def test_failure_is_not_confirmed(self) -> None:
        result = nh.to_booking_result({"data": {"appt": {"id": 9}}}, success=False)
        assert result.write_status == BookingWriteStatus.UNKNOWN.value
