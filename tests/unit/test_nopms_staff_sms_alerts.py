"""Which staff SMS alerts a no-PMS call triggers, and what they may contain.

Each alert type is a separate subscription, so a number receives only what it
opted into. Bodies are PHI-free by design: unlike the patient acknowledgement
these go to arbitrary staff numbers, so they carry triage metadata and a
dashboard link — never a name, DOB, or the AI summary text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.models.external_sms_notification_recipient import StaffSmsAlertType
from src.app.retell.webhooks import _nopms_alert_variables, _nopms_applicable_alerts

APPOINTMENT_REQUEST = StaffSmsAlertType.APPOINTMENT_REQUEST.value
CALL_SUMMARY = StaffSmsAlertType.CALL_SUMMARY.value
URGENT_ALERT = StaffSmsAlertType.URGENT_ALERT.value


def make_call(**overrides):
    base = dict(
        id="11111111-1111-1111-1111-111111111111",
        call_status="needs_booking",
        call_tags="needs_booking",
        requested_availability="Tuesday at 2 PM",
        is_new_patient=True,
        call_duration_seconds=142,
        patient_sentiment="Neutral",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def alerts(**overrides) -> list[str]:
    """Alert types this call triggers."""
    return _nopms_applicable_alerts(make_call(**overrides))


def variables(**overrides) -> dict[str, str]:
    """Render context handed to the staff SMS templates."""
    return _nopms_alert_variables(location_name="Olive Tree Dental", db_call=make_call(**overrides))


class TestWhichAlertsFire:
    def test_call_summary_fires_on_every_call(self):
        assert CALL_SUMMARY in alerts(call_status="no_action_needed", call_tags="no_action_needed")

    def test_appointment_request_needs_a_booking_classification(self):
        assert APPOINTMENT_REQUEST in alerts()
        assert APPOINTMENT_REQUEST not in alerts(
            call_status="no_action_needed", call_tags="no_action_needed"
        )

    @pytest.mark.parametrize("tags", ["emergency", "complaint", "emergency,needs_booking"])
    def test_urgent_fires_on_emergency_or_complaint(self, tags):
        assert URGENT_ALERT in alerts(call_status=tags.split(",")[0], call_tags=tags)

    def test_urgent_does_not_fire_on_a_routine_call(self):
        assert URGENT_ALERT not in alerts()

    def test_multi_tag_call_fires_several(self):
        fired = alerts(call_status="emergency", call_tags="emergency,needs_booking")
        assert set(fired) == {APPOINTMENT_REQUEST, URGENT_ALERT, CALL_SUMMARY}

    def test_secondary_tag_still_counts(self):
        """Primary status is needs_booking but emergency rides along as a tag."""
        assert URGENT_ALERT in alerts(
            call_status="needs_booking", call_tags="needs_booking,emergency"
        )


class TestVariablesCarryNoPhi:
    """Template variables are the only thing a staff SMS can interpolate, so
    the PHI guarantee lives here: no patient name, no DOB."""

    PHI = ("Hala", "Abu Lughod", "1961-08-29", "August-29-1961", "+15196976145")

    def test_no_patient_identifier_variables_exist(self):
        keys = set(variables())
        assert "patient_name" not in keys
        assert "date_of_birth" not in keys
        assert "dob" not in keys

    def test_no_identifier_leaks_into_any_value(self):
        rendered = " ".join(variables(call_status="emergency", call_tags="emergency").values())
        for identifier in self.PHI:
            assert identifier not in rendered

    def test_triage_metadata_is_present(self):
        v = variables()
        assert v["location_name"] == "Olive Tree Dental"
        assert v["call_status"] == "Needs Booking"
        assert v["duration"] == "2m 22s"
        assert v["sentiment"] == "Neutral"
        assert v["new_patient"] == "Yes"

    def test_urgency_names_the_kind(self):
        assert variables(call_status="emergency", call_tags="emergency")["urgency"] == "Emergency"
        assert variables(call_status="complaint", call_tags="complaint")["urgency"] == "Complaint"

    def test_missing_availability_is_labelled_not_blank(self):
        assert variables(requested_availability=None)["availability"] == "Not provided"

    def test_unknown_duration_does_not_render_zero(self):
        assert variables(call_duration_seconds=None)["duration"] == "unknown"


class TestDispatchLifecycle:
    """Staff alerts must run on ``call_analyzed``, not ``call_ended``.

    The alert set is derived from ``call_status`` / ``call_tags``, and those are
    written by the post-call pipeline that ``call_analyzed`` drives. Retell
    delivers ``call_ended`` first — with the Call row not yet created — so
    dispatching there skipped every alert silently.
    """

    def test_dispatch_helper_is_called_from_the_analyzed_pipeline(self):
        import inspect

        from src.app.retell import webhooks

        source = inspect.getsource(webhooks.process_retell_call_analyzed_event)
        assert "_queue_nopms_staff_sms" in source

    def test_call_ended_no_longer_dispatches_staff_alerts(self):
        import inspect

        from src.app.retell import webhooks

        source = inspect.getsource(webhooks)
        ended = source[source.index("async def process_retell_call_ended_event"):]
        ended = ended[: ended.index("async def process_retell_call_analyzed_event")] \
            if "async def process_retell_call_analyzed_event" in ended else ended
        assert "_queue_nopms_staff_sms" not in ended

    def test_helper_short_circuits_for_pms_tenants(self):
        """Every change in this area is no-PMS only."""
        import inspect

        from src.app.retell.webhooks import _queue_nopms_staff_sms

        source = inspect.getsource(_queue_nopms_staff_sms)
        assert "institution.has_pms" in source
        assert "return 0" in source
