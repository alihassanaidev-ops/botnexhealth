"""SMS template service — CRUD, rendering, and default template seeding.

The SMS counterpart to :mod:`src.app.services.email_template_service`. Templates
use Jinja2 for variable substitution and are populated from authoritative,
structured appointment data (provider name resolved from cached PMS providers,
booking time, patient), NOT from Retell's free-text message.

Autoescape is OFF here: an SMS is plain text, so HTML-escaping (``&`` → ``&amp;``)
would be wrong. The clinic-identity prefix and CASL/TCPA opt-out footer are added
downstream at send time by ``sms_privacy`` and are not part of the body.
"""

from __future__ import annotations

import logging
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateSyntaxError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.app.models.sms_template import SmsTemplate, SmsTemplateType

logger = logging.getLogger(__name__)

# Sandboxed, non-escaping Jinja env (plain-text SMS).
_jinja_env = Environment(loader=BaseLoader(), autoescape=False)


# ---------------------------------------------------------------------------
# Template variable definitions (used for the editor UI and sample previews)
# ---------------------------------------------------------------------------

TEMPLATE_VARIABLES: dict[str, list[dict[str, str]]] = {
    SmsTemplateType.APPOINTMENT_BOOKED.value: [
        {"key": "patient_name", "label": "Patient Name", "sample": "Jane Doe"},
        {"key": "location_name", "label": "Clinic / Location Name", "sample": "Downtown Dental"},
        {"key": "appointment_datetime", "label": "Appointment Date/Time", "sample": "Tue, Jul 2 at 9:30 AM"},
        {"key": "appointment_provider", "label": "Provider", "sample": "Dr. Smith"},
        {"key": "appointment_service", "label": "Service / Type", "sample": "Routine Cleaning"},
    ],
}


def get_sample_data(template_type: str) -> dict[str, str]:
    """Return sample variable values for preview rendering."""
    variables = TEMPLATE_VARIABLES.get(template_type, [])
    return {v["key"]: v["sample"] for v in variables}


# ---------------------------------------------------------------------------
# Default template bodies
# ---------------------------------------------------------------------------

SMS_DEFAULT_TEMPLATES: dict[str, dict[str, str]] = {
    SmsTemplateType.APPOINTMENT_BOOKED.value: {
        "name": "Appointment Booked Confirmation",
        # No clinic name / opt-out here — sms_privacy prepends clinic identity
        # and appends the CASL footer at send time.
        "body": (
            "Hi {{ patient_name }}, your appointment is confirmed for "
            "{{ appointment_datetime }} with {{ appointment_provider }}"
            "{% if appointment_service and appointment_service != 'Not provided' %}"
            " for {{ appointment_service }}{% endif %}. "
            "Please call us if you need to reschedule."
        ),
    },
}

# All SMS templates seed active by default (appointment confirmation is the
# core transactional message). Kept as a set for parity with the email service.
DEFAULT_INACTIVE_TYPES: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Service class
# ---------------------------------------------------------------------------

class SmsTemplateService:
    """CRUD and rendering operations for SMS templates."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_templates(self, institution_id: str) -> list[SmsTemplate]:
        """Get all SMS templates for an institution, seeding defaults if missing."""
        result = await self._session.execute(
            select(SmsTemplate)
            .where(SmsTemplate.institution_id == institution_id)
            .order_by(SmsTemplate.template_type)
        )
        templates = list(result.scalars().all())

        known_types = {t.value for t in SmsTemplateType}
        if known_types - {t.template_type for t in templates}:
            templates = await self._seed_defaults(institution_id)
            templates.sort(key=lambda t: t.template_type)

        return templates

    async def get_template_by_type(
        self, institution_id: str, template_type: str
    ) -> SmsTemplate | None:
        """Get a specific SMS template by type, seeding defaults if needed."""
        result = await self._session.execute(
            select(SmsTemplate).where(
                SmsTemplate.institution_id == institution_id,
                SmsTemplate.template_type == template_type,
            )
        )
        template = result.scalar_one_or_none()

        if template is None:
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
        body: str | None = None,
        is_active: bool | None = None,
    ) -> SmsTemplate | None:
        """Update an existing SMS template. Returns None if not found."""
        template = await self.get_template_by_type(institution_id, template_type)
        if not template:
            return None

        if name is not None:
            template.name = name
        if body is not None:
            template.body = body
        if is_active is not None:
            template.is_active = is_active

        self._session.add(template)
        await self._session.flush()
        await self._session.refresh(template)
        return template

    async def reset_template(
        self, institution_id: str, template_type: str
    ) -> SmsTemplate | None:
        """Reset an SMS template to its default content."""
        default = SMS_DEFAULT_TEMPLATES.get(template_type)
        if not default:
            return None

        return await self.update_template(
            institution_id,
            template_type,
            name=default["name"],
            body=default["body"],
            is_active=True,
        )

    async def _seed_defaults(self, institution_id: str) -> list[SmsTemplate]:
        """Create any missing default SMS templates for an institution.

        Idempotent: only inserts the types that are absent, so re-runs and
        newly added template types are both safe under the unique
        ``(institution_id, template_type)`` index.
        """
        existing = list(
            (
                await self._session.execute(
                    select(SmsTemplate).where(
                        SmsTemplate.institution_id == institution_id
                    )
                )
            ).scalars().all()
        )
        existing_types = {t.template_type for t in existing}

        for ttype, defaults in SMS_DEFAULT_TEMPLATES.items():
            if ttype in existing_types:
                continue
            self._session.add(
                SmsTemplate(
                    institution_id=institution_id,
                    template_type=ttype,
                    name=defaults["name"],
                    body=defaults["body"],
                    is_active=ttype not in DEFAULT_INACTIVE_TYPES,
                )
            )

        await self._session.flush()

        result = await self._session.execute(
            select(SmsTemplate)
            .where(SmsTemplate.institution_id == institution_id)
            .order_by(SmsTemplate.template_type)
        )
        return list(result.scalars().all())

    @staticmethod
    def render(template_str: str, variables: dict[str, Any]) -> str:
        """Render a template body with the given variables (plain text)."""
        return _jinja_env.from_string(template_str).render(**variables)

    @staticmethod
    def validate_template(template_str: str) -> str | None:
        """Return a syntax-error message for an invalid template, else None."""
        try:
            _jinja_env.from_string(template_str)
        except TemplateSyntaxError as exc:
            return str(exc)
        return None

    async def render_preview(
        self, institution_id: str, template_type: str
    ) -> dict[str, str] | None:
        """Render a saved template with sample data. None if not found."""
        template = await self.get_template_by_type(institution_id, template_type)
        if not template:
            return None
        return {"body": self.render(template.body, get_sample_data(template_type))}

    @staticmethod
    def render_preview_raw(*, body: str, template_type: str) -> dict[str, str]:
        """Render arbitrary (unsaved) body with sample data for live preview."""
        return {"body": SmsTemplateService.render(body, get_sample_data(template_type))}
