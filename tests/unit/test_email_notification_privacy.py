from __future__ import annotations

from src.app.models.email_template import EmailTemplateType
from src.app.services.email_notification_service import (
    SAFE_SUMMARY_PLACEHOLDER,
    _build_template_variables,
)
from src.app.services.email_template_service import (
    APPOINTMENT_REQUEST_DEFAULT,
    DEFAULT_TEMPLATES,
    EmailTemplateService,
)


def test_email_template_variables_never_include_call_summary_phi() -> None:
    variables = _build_template_variables(
        {
            "location_name": "Downtown Dental",
            "caller_phone_masked": "******4321",
            "duration_seconds": 120,
            "primary_tag": "needs_callback",
            "tags": ["needs_callback"],
            "summary": "Jane Smith called about treatment and insurance.",
        }
    )

    assert variables["summary"] == SAFE_SUMMARY_PLACEHOLDER
    assert "Jane Smith" not in str(variables)
    assert "treatment" not in str(variables)


def test_default_email_templates_render_safe_dashboard_notice_not_summary_phi() -> None:
    variables = _build_template_variables(
        {
            "location_name": "Downtown Dental",
            "caller_phone_masked": "******4321",
            "duration_seconds": 120,
            "primary_tag": "emergency",
            "tags": ["emergency"],
            "summary": "John Smith has severe pain and shared diagnosis details.",
            "appointment_patient_redacted": "J*** S***",
        }
    )

    for template_type in (
        EmailTemplateType.CALL_SUMMARY.value,
        EmailTemplateType.URGENT_ALERT.value,
        EmailTemplateType.APPOINTMENT_CONFIRMATION.value,
    ):
        defaults = DEFAULT_TEMPLATES[template_type]
        rendered_text = EmailTemplateService.render(defaults["text_body"], variables)
        rendered_html = EmailTemplateService.render(defaults["html_body"], variables)

        assert SAFE_SUMMARY_PLACEHOLDER in rendered_text + rendered_html
        assert "John Smith" not in rendered_text + rendered_html
        assert "diagnosis" not in rendered_text + rendered_html


def test_no_pms_appointment_request_default_uses_request_wording() -> None:
    """No-PMS appointment notifications must read as a request, not a confirmation.

    The send-time swap keys off the subject differing from the confirmation
    default, so that invariant is locked here along with request wording.
    """
    conf = DEFAULT_TEMPLATES[EmailTemplateType.APPOINTMENT_CONFIRMATION.value]
    req = APPOINTMENT_REQUEST_DEFAULT

    # The swap distinguishes request vs confirmation by subject — must differ.
    assert req["subject_template"] != conf["subject_template"]

    variables = _build_template_variables(
        {
            "location_name": "Downtown Dental",
            "caller_phone_masked": "******4321",
            "duration_seconds": 90,
            "primary_tag": "appointment_booked",
            "tags": ["appointment_booked"],
            "summary": "Jane Smith wants a cleaning next week.",
            "appointment_patient_redacted": "J*** S***",
        }
    )
    text = EmailTemplateService.render(req["text_body"], variables)
    html = EmailTemplateService.render(req["html_body"], variables)
    subject = EmailTemplateService.render(req["subject_template"], variables)

    blob = (text + html + subject).lower()
    assert "request" in blob
    assert "manual" in blob  # "pending manual booking" / "book it manually"
    # Still PHI-safe — never leak the call summary.
    assert "jane smith" not in blob
    assert "cleaning next week" not in blob
