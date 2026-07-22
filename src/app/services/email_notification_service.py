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
from src.app.services.sms_privacy import hash_for_logging

logger = logging.getLogger(__name__)

SAFE_SUMMARY_PLACEHOLDER = "Available in the authenticated dashboard."


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


def _build_template_variables(
    payload: dict[str, Any],
    *,
    patient_facing: bool = False,
) -> dict[str, str]:
    """Convert raw call payload into template variables.

    Both staff and patient emails render the full collected patient name now
    that unscrubbed call data flows end-to-end. ``appointment_patient_redacted``
    is still read as a fallback for any legacy caller that supplies only that
    key. (``patient_facing`` is retained for callers/other rendering logic.)
    """
    tags = payload.get("tags") or []
    patient_name = (
        payload.get("appointment_patient_name")
        or payload.get("appointment_patient_redacted")
        or "Not provided"
    )
    return {
        "location_name": payload.get("location_name") or "Clinic",
        "caller_phone": payload.get("caller_phone_masked") or "Unknown",
        "duration": format_duration(payload.get("duration_seconds")),
        "primary_tag": _tag_label(payload.get("primary_tag")),
        "all_tags": ", ".join(tags) if tags else "None",
        # Legacy/custom templates may still contain {{ summary }}. Never pass
        # call.summary into email; keep details behind authenticated dashboard
        # access where RBAC, tenant scope, and audit controls apply.
        "summary": SAFE_SUMMARY_PLACEHOLDER,
        "patient_name": patient_name,
        "appointment_datetime": payload.get("appointment_datetime") or "Not provided",
        "appointment_provider": payload.get("appointment_provider") or "Not provided",
        "appointment_service": payload.get("appointment_service") or "Not provided",
        # No-PMS PHI-free triage variables (blank/"Not provided" for integrated
        # tenants, which don't populate them).
        "availability": payload.get("availability") or "Not provided",
        "new_patient": payload.get("new_patient") or "Not provided",
        "is_emergency": payload.get("is_emergency") or "No",
        "call_status": payload.get("call_status_label") or _tag_label(payload.get("primary_tag")),
        # Deep link to the RBAC-protected call detail; empty string when the
        # frontend base URL is not configured (template hides the CTA).
        "dashboard_link": payload.get("dashboard_link") or "",
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
        patient_facing: bool = False,
        appointment_pending: bool = False,
    ) -> None:
        """Send a notification email using the appropriate template.

        Attempts to load a custom template from the DB for the institution.
        Falls back to the built-in default if none is found or DB is unavailable.

        When ``patient_facing`` is True the email goes to the patient and
        appointment details are rendered unredacted.
        """
        api_key = settings.resend_api_key
        sender = settings.resend_from_email
        if not api_key or not sender:
            raise RuntimeError(
                "Resend is not configured (RESEND_API_KEY / RESEND_FROM_EMAIL)"
            )
        if not recipients:
            raise RuntimeError("No recipients for call notification")

        variables = _build_template_variables(payload, patient_facing=patient_facing)

        # Try loading custom template from DB
        subject_tpl: str | None = None
        html_tpl: str | None = None
        text_tpl: str | None = None

        if institution_id:
            try:
                from src.app.database import get_system_db_session

                async with get_system_db_session(
                    "celery",
                    institution_id=institution_id,
                ) as session:
                    svc = EmailTemplateService(session)
                    template = await svc.get_template_by_type(
                        institution_id, template_type
                    )
                    if template and template.is_active:
                        subject_tpl = template.subject_template
                        html_tpl = template.html_body
                        text_tpl = template.text_body
            except Exception:
                logger.warning(
                    "Failed to load email template from DB, using default: type=%s institution_hash=%s",
                    template_type,
                    hash_for_logging(institution_id),
                )

        # Fall back to in-code defaults when no active DB template exists.
        if not subject_tpl:
            defaults = DEFAULT_TEMPLATES.get(
                template_type, DEFAULT_TEMPLATES[EmailTemplateType.CALL_SUMMARY.value]
            )
            subject_tpl = defaults["subject_template"]
            html_tpl = defaults["html_body"]
            text_tpl = defaults["text_body"]

        # No-PMS requests now route to the dedicated ``appointment_request``
        # template type upstream (see tasks.notifications), so the DB lookup and
        # in-code fallback above already resolve the PHI-free request template —
        # no post-hoc swap needed.

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
                    "Resend send failed: status=%s body_hash=%s",
                    response.status_code,
                    hash_for_logging(response.text),
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
