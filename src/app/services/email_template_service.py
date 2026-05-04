"""Email template service — CRUD, rendering, and default template seeding.

Templates use Jinja2 syntax for variable substitution. Each institution gets
default templates on first access which can then be customized.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from jinja2 import BaseLoader, Environment, TemplateSyntaxError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.email_template import EmailTemplate, EmailTemplateType

logger = logging.getLogger(__name__)

# Jinja2 environment with sandboxed rendering (no file access)
_jinja_env = Environment(loader=BaseLoader(), autoescape=True)


# ---------------------------------------------------------------------------
# Template variable definitions (used for editor UI and sample data)
# ---------------------------------------------------------------------------

TEMPLATE_VARIABLES: dict[str, list[dict[str, str]]] = {
    EmailTemplateType.CALL_SUMMARY.value: [
        {"key": "location_name", "label": "Location Name", "sample": "Downtown Dental"},
        {"key": "caller_phone", "label": "Caller Phone (masked)", "sample": "******4321"},
        {"key": "duration", "label": "Call Duration", "sample": "2m 15s"},
        {"key": "primary_tag", "label": "Primary Tag", "sample": "appointment booked"},
        {"key": "all_tags", "label": "All Tags", "sample": "appointment booked, new patient"},
        {"key": "summary", "label": "Dashboard Summary Notice", "sample": "Available in the authenticated dashboard."},
        {"key": "patient_name", "label": "Patient Name (redacted)", "sample": "J*** D***"},
        {"key": "appointment_datetime", "label": "Appointment Date/Time", "sample": "2026-03-28 2:30 PM"},
        {"key": "appointment_provider", "label": "Appointment Provider", "sample": "Dr. Smith"},
        {"key": "appointment_service", "label": "Appointment Service", "sample": "Routine Cleaning"},
    ],
    EmailTemplateType.URGENT_ALERT.value: [
        {"key": "location_name", "label": "Location Name", "sample": "Downtown Dental"},
        {"key": "caller_phone", "label": "Caller Phone (masked)", "sample": "******4321"},
        {"key": "duration", "label": "Call Duration", "sample": "4m 30s"},
        {"key": "primary_tag", "label": "Primary Tag", "sample": "emergency"},
        {"key": "all_tags", "label": "All Tags", "sample": "emergency, pain"},
        {"key": "summary", "label": "Dashboard Summary Notice", "sample": "Available in the authenticated dashboard."},
        {"key": "patient_name", "label": "Patient Name (redacted)", "sample": "J*** D***"},
        {"key": "appointment_datetime", "label": "Appointment Date/Time", "sample": "Not provided"},
        {"key": "appointment_provider", "label": "Appointment Provider", "sample": "Not provided"},
        {"key": "appointment_service", "label": "Appointment Service", "sample": "Emergency Visit"},
    ],
    EmailTemplateType.APPOINTMENT_CONFIRMATION.value: [
        {"key": "location_name", "label": "Location Name", "sample": "Downtown Dental"},
        {"key": "caller_phone", "label": "Caller Phone (masked)", "sample": "******4321"},
        {"key": "duration", "label": "Call Duration", "sample": "3m 45s"},
        {"key": "primary_tag", "label": "Primary Tag", "sample": "appointment booked"},
        {"key": "all_tags", "label": "All Tags", "sample": "appointment booked, returning patient"},
        {"key": "summary", "label": "Dashboard Summary Notice", "sample": "Available in the authenticated dashboard."},
        {"key": "patient_name", "label": "Patient Name (redacted)", "sample": "J*** D***"},
        {"key": "appointment_datetime", "label": "Appointment Date/Time", "sample": "2026-03-28 2:30 PM"},
        {"key": "appointment_provider", "label": "Appointment Provider", "sample": "Dr. Smith"},
        {"key": "appointment_service", "label": "Appointment Service", "sample": "Follow-up Consultation"},
    ],
}


def get_sample_data(template_type: str) -> dict[str, str]:
    """Return sample variable values for preview rendering."""
    variables = TEMPLATE_VARIABLES.get(template_type, [])
    return {v["key"]: v["sample"] for v in variables}


# ---------------------------------------------------------------------------
# Default template HTML/text bodies
# ---------------------------------------------------------------------------

_STYLES = {
    "body_bg": "#09090b",
    "card_bg": "#18181b",
    "border": "#27272a",
    "text_primary": "#fafafa",
    "text_secondary": "#e4e4e7",
    "text_muted": "#a1a1aa",
    "text_dim": "#71717a",
    "text_footer": "#3f3f46",
    "accent": "#7c3aed",
    "urgent_bg": "#991b1b",
    "urgent_text": "#fef2f2",
}

_ROW_STYLE = f"padding:10px 0;border-bottom:1px solid {_STYLES['border']};font-size:14px;"
_LABEL_STYLE = f"{_ROW_STYLE}color:{_STYLES['text_muted']};width:120px;vertical-align:top;"
_VALUE_STYLE = f"{_ROW_STYLE}color:{_STYLES['text_secondary']};"


def _wrap_email(heading: str, subheading: str, inner_html: str) -> str:
    """Wrap inner content in the standard email shell (dark theme)."""
    return (
        '<!DOCTYPE html><html><head><meta charset="UTF-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
        "</head>"
        f'<body style="margin:0;padding:0;background-color:{_STYLES["body_bg"]};'
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        f' style="background-color:{_STYLES["body_bg"]};padding:40px 20px;"><tr><td align="center">'
        '<table role="presentation" width="520" cellpadding="0" cellspacing="0"'
        ' style="max-width:520px;width:100%;">'
        # Brand
        '<tr><td align="center" style="padding-bottom:32px;">'
        f'<span style="font-size:24px;font-weight:700;color:#ffffff;letter-spacing:-0.5px;">'
        "{{ location_name }}</span></td></tr>"
        # Card
        f'<tr><td style="background-color:{_STYLES["card_bg"]};border:1px solid {_STYLES["border"]};'
        'border-radius:12px;padding:40px 36px;">'
        # Heading
        f'<h2 style="margin:0 0 4px;font-size:20px;font-weight:600;color:{_STYLES["text_primary"]};">{heading}</h2>'
        f'<p style="margin:0 0 24px;font-size:13px;color:{_STYLES["text_dim"]};">{subheading}</p>'
        # Inner content
        f"{inner_html}"
        # End card
        "</td></tr>"
        # Footer
        '<tr><td align="center" style="padding-top:28px;">'
        f'<p style="margin:0;font-size:12px;color:{_STYLES["text_footer"]};">'
        "&copy; {{ location_name }}</p>"
        "</td></tr>"
        "</table></td></tr></table></body></html>"
    )


def _call_details_table() -> str:
    """Shared call details rows used across templates."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="border-collapse:collapse;">'
        f'<tr><td style="{_LABEL_STYLE}">Caller</td>'
        f'<td style="{_VALUE_STYLE}font-family:monospace;">{{{{ caller_phone }}}}</td></tr>'
        f'<tr><td style="{_LABEL_STYLE}">Duration</td>'
        f'<td style="{_VALUE_STYLE}">{{{{ duration }}}}</td></tr>'
        f'<tr><td style="{_LABEL_STYLE}">Primary Tag</td>'
        f'<td style="{_VALUE_STYLE}">'
        f'<span style="display:inline-block;background:{_STYLES["accent"]};color:#fff;padding:2px 10px;'
        f'border-radius:12px;font-size:12px;font-weight:600;">{{{{ primary_tag }}}}</span></td></tr>'
        f'<tr><td style="{_LABEL_STYLE}">All Tags</td>'
        f'<td style="{_VALUE_STYLE}font-size:13px;">{{{{ all_tags }}}}</td></tr>'
        "</table>"
    )


def _appointment_section() -> str:
    """Shared appointment confirmation section."""
    return (
        f'<div style="margin-top:24px;padding:20px;background:{_STYLES["body_bg"]};border:1px solid {_STYLES["border"]};'
        'border-radius:8px;">'
        f'<div style="font-size:14px;font-weight:600;color:{_STYLES["text_primary"]};margin-bottom:14px;">'
        "Appointment Confirmation</div>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
        ' style="border-collapse:collapse;">'
        f'<tr><td style="{_LABEL_STYLE}">Patient</td>'
        f'<td style="{_VALUE_STYLE}">{{{{ patient_name }}}}</td></tr>'
        f'<tr><td style="{_LABEL_STYLE}">Date/Time</td>'
        f'<td style="{_VALUE_STYLE}">{{{{ appointment_datetime }}}}</td></tr>'
        f'<tr><td style="{_LABEL_STYLE}">Provider</td>'
        f'<td style="{_VALUE_STYLE}">{{{{ appointment_provider }}}}</td></tr>'
        f'<tr><td style="{_LABEL_STYLE}border-bottom:none;">Service</td>'
        f'<td style="{_VALUE_STYLE}border-bottom:none;">{{{{ appointment_service }}}}</td></tr>'
        "</table></div>"
    )


def _urgent_banner() -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
        '<tr><td style="padding:0 0 16px;">'
        f'<div style="padding:12px 16px;background:{_STYLES["urgent_bg"]};color:{_STYLES["urgent_text"]};font-weight:600;'
        'border-radius:8px;font-size:13px;text-align:center;">'
        "URGENT: Emergency or complaint call requires immediate attention.</div>"
        "</td></tr></table>"
    )


# --- Default templates ---

DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    EmailTemplateType.CALL_SUMMARY.value: {
        "name": "Standard Call Summary",
        "subject_template": "{{ location_name }} call summary ({{ primary_tag }})",
        "html_body": _wrap_email(
            "Call Summary",
            "A call has been processed and classified.",
            _call_details_table() + _appointment_section(),
        ),
        "text_body": (
            "Call Summary\n\n"
            "Caller Phone: {{ caller_phone }}\n"
            "Duration: {{ duration }}\n"
            "Primary Tag: {{ primary_tag }}\n"
            "All Tags: {{ all_tags }}\n"
            "Details: {{ summary }}\n\n"
            "Appointment Confirmation\n"
            "Patient: {{ patient_name }}\n"
            "Date/Time: {{ appointment_datetime }}\n"
            "Provider: {{ appointment_provider }}\n"
            "Service: {{ appointment_service }}\n"
        ),
    },
    EmailTemplateType.URGENT_ALERT.value: {
        "name": "Urgent Call Alert",
        "subject_template": "URGENT: {{ location_name }} call alert ({{ primary_tag }})",
        "html_body": _wrap_email(
            "Urgent Call Alert",
            "An urgent call requires immediate attention.",
            _urgent_banner() + _call_details_table() + _appointment_section(),
        ),
        "text_body": (
            "URGENT: Call Alert\n\n"
            "Caller Phone: {{ caller_phone }}\n"
            "Duration: {{ duration }}\n"
            "Primary Tag: {{ primary_tag }}\n"
            "All Tags: {{ all_tags }}\n"
            "Details: {{ summary }}\n\n"
            "Appointment Confirmation\n"
            "Patient: {{ patient_name }}\n"
            "Date/Time: {{ appointment_datetime }}\n"
            "Provider: {{ appointment_provider }}\n"
            "Service: {{ appointment_service }}\n"
        ),
    },
    EmailTemplateType.APPOINTMENT_CONFIRMATION.value: {
        "name": "Appointment Confirmation",
        "subject_template": "{{ location_name }} — Appointment booked ({{ appointment_service }})",
        "html_body": _wrap_email(
            "Appointment Booked",
            "A new appointment has been scheduled via phone.",
            _appointment_section()
            + (
                f'<div style="margin-top:20px;">'
                '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
                ' style="border-collapse:collapse;">'
                f'<tr><td style="{_LABEL_STYLE}">Caller</td>'
                f'<td style="{_VALUE_STYLE}font-family:monospace;">{{{{ caller_phone }}}}</td></tr>'
                f'<tr><td style="{_LABEL_STYLE}">Duration</td>'
                f'<td style="{_VALUE_STYLE}">{{{{ duration }}}}</td></tr>'
                "</table></div>"
            ),
        ),
        "text_body": (
            "Appointment Booked\n\n"
            "Patient: {{ patient_name }}\n"
            "Date/Time: {{ appointment_datetime }}\n"
            "Provider: {{ appointment_provider }}\n"
            "Service: {{ appointment_service }}\n\n"
            "Call Details\n"
            "Caller Phone: {{ caller_phone }}\n"
            "Duration: {{ duration }}\n"
            "Details: {{ summary }}\n"
        ),
    },
}


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class EmailTemplateService:
    """CRUD and rendering operations for email templates."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_templates(self, institution_id: str) -> list[EmailTemplate]:
        """Get all templates for an institution, seeding defaults if none exist."""
        result = await self._session.execute(
            select(EmailTemplate)
            .where(EmailTemplate.institution_id == institution_id)
            .order_by(EmailTemplate.template_type)
        )
        templates = list(result.scalars().all())

        if not templates:
            templates = await self._seed_defaults(institution_id)

        return templates

    async def get_template_by_type(
        self, institution_id: str, template_type: str
    ) -> EmailTemplate | None:
        """Get a specific template by type, seeding defaults if needed."""
        result = await self._session.execute(
            select(EmailTemplate).where(
                EmailTemplate.institution_id == institution_id,
                EmailTemplate.template_type == template_type,
            )
        )
        template = result.scalar_one_or_none()

        if template is None:
            # Seed all defaults and return the requested one
            templates = await self._seed_defaults(institution_id)
            for t in templates:
                if t.template_type == template_type:
                    return t

        return template

    async def update_template(
        self,
        institution_id: str,
        template_type: str,
        *,
        name: str | None = None,
        subject_template: str | None = None,
        html_body: str | None = None,
        text_body: str | None = None,
        is_active: bool | None = None,
    ) -> EmailTemplate | None:
        """Update an existing template. Returns None if not found."""
        template = await self.get_template_by_type(institution_id, template_type)
        if not template:
            return None

        if name is not None:
            template.name = name
        if subject_template is not None:
            template.subject_template = subject_template
        if html_body is not None:
            template.html_body = html_body
        if text_body is not None:
            template.text_body = text_body
        if is_active is not None:
            template.is_active = is_active

        self._session.add(template)
        await self._session.flush()
        await self._session.refresh(template)
        return template

    async def reset_template(
        self, institution_id: str, template_type: str
    ) -> EmailTemplate | None:
        """Reset a template to its default content."""
        default = DEFAULT_TEMPLATES.get(template_type)
        if not default:
            return None

        return await self.update_template(
            institution_id,
            template_type,
            name=default["name"],
            subject_template=default["subject_template"],
            html_body=default["html_body"],
            text_body=default["text_body"],
            is_active=True,
        )

    async def _seed_defaults(self, institution_id: str) -> list[EmailTemplate]:
        """Create default templates for an institution."""
        templates: list[EmailTemplate] = []
        for ttype, defaults in DEFAULT_TEMPLATES.items():
            template = EmailTemplate(
                id=str(uuid4()),
                institution_id=institution_id,
                template_type=ttype,
                name=defaults["name"],
                subject_template=defaults["subject_template"],
                html_body=defaults["html_body"],
                text_body=defaults["text_body"],
                is_active=True,
            )
            self._session.add(template)
            templates.append(template)

        await self._session.flush()
        return templates

    # -- Rendering -----------------------------------------------------------

    @staticmethod
    def render(template_str: str, variables: dict[str, Any]) -> str:
        """Render a Jinja2 template string with the given variables."""
        tpl = _jinja_env.from_string(template_str)
        return tpl.render(**variables)

    @staticmethod
    def validate_template(template_str: str) -> str | None:
        """Validate Jinja2 syntax. Returns error message or None if valid."""
        try:
            _jinja_env.parse(template_str)
            return None
        except TemplateSyntaxError as exc:
            return f"Line {exc.lineno}: {exc.message}" if exc.lineno else str(exc.message)

    async def render_preview(
        self, institution_id: str, template_type: str
    ) -> dict[str, str] | None:
        """Render a template with sample data for preview."""
        template = await self.get_template_by_type(institution_id, template_type)
        if not template:
            return None

        sample = get_sample_data(template_type)
        return {
            "subject": self.render(template.subject_template, sample),
            "html": self.render(template.html_body, sample),
            "text": self.render(template.text_body, sample),
        }

    @staticmethod
    def render_preview_raw(
        *,
        subject_template: str,
        html_body: str,
        text_body: str,
        template_type: str,
    ) -> dict[str, str]:
        """Render arbitrary template content with sample data (for live preview)."""
        sample = get_sample_data(template_type)
        render = EmailTemplateService.render
        return {
            "subject": render(subject_template, sample),
            "html": render(html_body, sample),
            "text": render(text_body, sample),
        }
