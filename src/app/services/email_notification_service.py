"""Email notification helpers and Resend sender.

Loads templates from the database when available, falling back to defaults.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from src.app.config import settings
from src.app.models.email_template import EmailTemplateType
from src.app.services.email_template_service import (
    DEFAULT_TEMPLATES,
    EmailTemplateService,
)
from src.app.services.sms_privacy import sanitize_provider_error

logger = logging.getLogger(__name__)


def mask_phone(phone: str | None) -> str:
    """Mask phone number for alerts while keeping minimal operator context."""
    if not phone:
        return "Unknown"

    digits = "".join(ch for ch in phone if ch.isdigit())
    if len(digits) <= 4:
        return "****"
    return f"{'*' * (len(digits) - 4)}{digits[-4:]}"


def redact_patient_name(name: str | None) -> str:
    """Redact patient name before sending email."""
    if not name:
        return "Not provided"

    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "Not provided"
    redacted = [f"{p[0]}***" for p in parts]
    return " ".join(redacted)


def format_duration(duration_seconds: int | None) -> str:
    if duration_seconds is None:
        return "Unknown"
    minutes, seconds = divmod(max(0, duration_seconds), 60)
    return f"{minutes}m {seconds}s"


def _tag_label(tag: str | None) -> str:
    if not tag:
        return "unclassified"
    return tag.replace("_", " ")


def resolve_template_type(*, is_urgent: bool, is_appointment_booked: bool) -> str:
    """Determine which email template type to use based on call classification."""
    if is_urgent:
        return EmailTemplateType.URGENT_ALERT.value
    if is_appointment_booked:
        return EmailTemplateType.APPOINTMENT_CONFIRMATION.value
    return EmailTemplateType.CALL_SUMMARY.value


def _build_template_variables(payload: dict[str, Any]) -> dict[str, str]:
    """Convert raw call payload into template variables."""
    tags = payload.get("tags") or []
    return {
        "location_name": payload.get("location_name") or "Clinic",
        "caller_phone": payload.get("caller_phone_masked") or "Unknown",
        "duration": format_duration(payload.get("duration_seconds")),
        "primary_tag": _tag_label(payload.get("primary_tag")),
        "all_tags": ", ".join(tags) if tags else "None",
        "summary": payload.get("summary") or "No summary available.",
        "patient_name": payload.get("appointment_patient_redacted") or "Not provided",
        "appointment_datetime": payload.get("appointment_datetime") or "Not provided",
        "appointment_provider": payload.get("appointment_provider") or "Not provided",
        "appointment_service": payload.get("appointment_service") or "Not provided",
    }


class EmailNotificationService:
    """Sends call alert emails through Resend using DB-backed templates."""

    async def send_notification(
        self,
        *,
        recipients: list[str],
        payload: dict[str, Any],
        idempotency_key: str,
        template_type: str,
        institution_id: str | None = None,
    ) -> None:
        """Send a notification email using the appropriate template.

        Attempts to load a custom template from the DB for the institution.
        Falls back to the built-in default if none is found or DB is unavailable.
        """
        api_key = settings.resend_api_key
        sender = settings.resend_from_email
        if not api_key or not sender:
            raise RuntimeError("Resend is not configured (RESEND_API_KEY / RESEND_FROM_EMAIL)")
        if not recipients:
            raise RuntimeError("No recipients for call notification")

        variables = _build_template_variables(payload)

        # Try loading custom template from DB
        subject_tpl: str | None = None
        html_tpl: str | None = None
        text_tpl: str | None = None

        if institution_id:
            try:
                from src.app.database import get_db_session

                async with get_db_session() as session:
                    svc = EmailTemplateService(session)
                    template = await svc.get_template_by_type(institution_id, template_type)
                    if template and template.is_active:
                        subject_tpl = template.subject_template
                        html_tpl = template.html_body
                        text_tpl = template.text_body
            except Exception:
                logger.warning(
                    "Failed to load email template from DB, using default: type=%s institution=%s",
                    template_type,
                    institution_id,
                    exc_info=True,
                )

        # Fall back to defaults
        if not subject_tpl:
            defaults = DEFAULT_TEMPLATES.get(template_type, DEFAULT_TEMPLATES[EmailTemplateType.CALL_SUMMARY.value])
            subject_tpl = defaults["subject_template"]
            html_tpl = defaults["html_body"]
            text_tpl = defaults["text_body"]

        # Render templates
        render = EmailTemplateService.render
        subject = render(subject_tpl, variables)
        html = render(html_tpl, variables)
        text = render(text_tpl, variables)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        request_body: dict[str, Any] = {
            "from": sender,
            "to": recipients,
            "subject": subject,
            "html": html,
            "text": text,
        }
        if settings.resend_reply_to:
            request_body["reply_to"] = settings.resend_reply_to

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers=headers,
                json=request_body,
            )
            if response.status_code >= 400:
                logger.error(
                    "Resend send failed: status=%s body=%s",
                    response.status_code,
                    sanitize_provider_error(response.text, max_length=500),
                )
                response.raise_for_status()

    # Backwards-compatible alias for existing callers
    async def send_call_created_notification(
        self,
        *,
        recipients: list[str],
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        """Legacy method — routes to the correct template based on payload."""
        is_urgent = bool(payload.get("is_urgent"))
        # Check if this is an appointment-booked call
        primary_tag = (payload.get("primary_tag") or "").lower().replace(" ", "_")
        is_appointment = primary_tag == "appointment_booked"

        template_type = resolve_template_type(
            is_urgent=is_urgent,
            is_appointment_booked=is_appointment,
        )

        await self.send_notification(
            recipients=recipients,
            payload=payload,
            idempotency_key=idempotency_key,
            template_type=template_type,
            institution_id=payload.get("institution_id"),
        )
