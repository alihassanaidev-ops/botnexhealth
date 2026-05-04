from __future__ import annotations

from src.app.models.email_template import EmailTemplateType
from src.app.services.email_notification_service import (
    SAFE_SUMMARY_PLACEHOLDER,
    _build_template_variables,
)
from src.app.services.email_template_service import DEFAULT_TEMPLATES, EmailTemplateService


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
