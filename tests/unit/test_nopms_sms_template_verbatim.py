"""No-PMS SMS bodies are sent exactly as the institution admin saved them.

The SMS template editor is a no-PMS-only screen (the dashboard route sits
behind ``NoPmsGuard``), and it is the only place that copy can be edited. So
the sends it drives — the staff alerts and the patient request acknowledgement
— must not have an opt-out footer bolted on underneath at send time: an admin
who cannot see or edit that line should not be shipping it.

The PMS ``appointment_booked`` confirmation is deliberately left alone and
still picks up the CASL/TCPA footer.
"""

from __future__ import annotations

import inspect

from src.app.models.sms_template import SmsTemplateType
from src.app.services.sms_privacy import CASL_FOOTER, prepare_outbound_sms_body
from src.app.services.sms_template_service import SMS_DEFAULT_TEMPLATES

NOPMS_TEMPLATE_TYPES = (
    SmsTemplateType.PATIENT_APPOINTMENT_REQUEST.value,
    SmsTemplateType.CALL_SUMMARY.value,
    SmsTemplateType.URGENT_ALERT.value,
    SmsTemplateType.APPOINTMENT_REQUEST.value,
)


class TestFooterSuppression:
    def test_opting_out_leaves_the_body_untouched(self):
        body = "Call handled at Olive Tree Dental. Status: Needs Booking."
        assert prepare_outbound_sms_body(
            body=body, include_opt_out_footer=False, include_clinic_identity=False
        ) == body

    def test_opting_in_still_appends(self):
        prepared = prepare_outbound_sms_body(
            body="Hi there.", clinic_identity="Olive Tree Dental", include_opt_out_footer=True
        )
        assert prepared.endswith(CASL_FOOTER)

    def test_an_admin_authored_opt_out_line_survives_verbatim(self):
        """Admins own the wording, including their own opt-out sentence."""
        body = "Olive Tree Dental: text STOP to unsubscribe."
        assert prepare_outbound_sms_body(
            body=body, include_opt_out_footer=False, include_clinic_identity=False
        ) == body


class TestSendPathsOptOut:
    """The two no-PMS enqueue sites suppress the footer; the PMS one does not."""

    def test_staff_alerts_send_the_template_verbatim(self):
        from src.app.retell import webhooks

        source = inspect.getsource(webhooks.process_retell_call_analyzed_event)
        # Isolate the enqueue call itself, between the fan-out loop and the
        # log line that follows it.
        enqueue_call = source.split("for _phone, _body in staff_sms_pending")[1]
        enqueue_call = enqueue_call.split("if staff_sms_pending")[0]
        assert "include_opt_out_footer=False" in enqueue_call
        assert "include_clinic_identity=False" in enqueue_call

    def test_patient_ack_follows_has_pms(self):
        from src.app.retell import webhooks

        source = inspect.getsource(webhooks.process_retell_call_ended_event)
        assert "include_opt_out_footer=institution.has_pms" in source
        assert "include_clinic_identity=institution.has_pms" in source

    def test_enqueue_forwards_the_flag_to_the_worker(self, monkeypatch):
        from src.app.tasks import sms as sms_task

        captured: dict = {}

        def fake_apply_async(**kwargs):
            captured.update(kwargs)

        monkeypatch.setattr(sms_task.settings, "celery_broker_url", "memory://")
        monkeypatch.setattr(sms_task.send_sms_message, "apply_async", fake_apply_async)

        sms_task.enqueue_auto_sms(
            from_number="+15550001111",
            to_number="+15550002222",
            body="Call handled at Olive Tree Dental.",
            institution_location_id="22222222-2222-2222-2222-222222222222",
            include_opt_out_footer=False,
            include_clinic_identity=False,
        )

        assert captured["kwargs"]["include_opt_out_footer"] is False
        assert captured["kwargs"]["include_clinic_identity"] is False

    def test_enqueue_still_defaults_to_appending(self, monkeypatch):
        """An omitted flag must not silently drop the footer on other callers."""
        from src.app.tasks import sms as sms_task

        captured: dict = {}
        monkeypatch.setattr(sms_task.settings, "celery_broker_url", "memory://")
        monkeypatch.setattr(
            sms_task.send_sms_message, "apply_async", lambda **kw: captured.update(kw)
        )

        sms_task.enqueue_auto_sms(
            from_number="+15550001111",
            to_number="+15550002222",
            body="Your appointment is confirmed.",
            institution_location_id="22222222-2222-2222-2222-222222222222",
        )

        assert captured["kwargs"]["include_opt_out_footer"] is True
        assert captured["kwargs"]["include_clinic_identity"] is True


class TestDefaultTemplateBodies:
    def test_no_pms_defaults_carry_no_opt_out_copy(self):
        """Nothing appends it now, so a seeded default must not imply one."""
        for template_type in NOPMS_TEMPLATE_TYPES:
            body = SMS_DEFAULT_TEMPLATES[template_type]["body"].lower()
            assert "reply stop" not in body, template_type
            assert "opt out" not in body, template_type


class TestPmsSendIsUnchanged:
    """Kadri has no SMS template editor, so its wording must not shift.

    The PMS confirmation is the one send that still relies on the platform to
    identify the clinic and carry the opt-out copy. Both flags default on, so
    this asserts the untouched defaults still produce the old shape.
    """

    def test_pms_confirmation_keeps_prefix_and_footer(self):
        prepared = prepare_outbound_sms_body(
            body="Hi Jane, your appointment is confirmed for Tue Sep 2 at 10:00.",
            clinic_identity="Dr. Kareem Kadri",
        )

        assert prepared.startswith("Dr. Kareem Kadri: ")
        assert prepared.endswith(CASL_FOOTER)

    def test_the_prefix_is_not_doubled_when_the_body_already_opens_with_it(self):
        prepared = prepare_outbound_sms_body(
            body="Dr. Kareem Kadri: your appointment is confirmed.",
            clinic_identity="Dr. Kareem Kadri",
        )

        assert prepared.count("Dr. Kareem Kadri") == 1
