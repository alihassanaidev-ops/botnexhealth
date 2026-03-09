"""Email notification helpers and Resend sender."""

from __future__ import annotations

import logging
from html import escape
from typing import Any

import httpx

from src.app.config import settings

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


class EmailNotificationService:
    """Sends call alert emails through Resend."""

    async def send_call_created_notification(
        self,
        *,
        recipients: list[str],
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> None:
        api_key = settings.resend_api_key
        sender = settings.resend_from_email
        if not api_key or not sender:
            raise RuntimeError("Resend is not configured (RESEND_API_KEY / RESEND_FROM_EMAIL)")
        if not recipients:
            raise RuntimeError("No recipients for call notification")

        location_name = payload.get("location_name") or "Clinic"
        primary_tag = payload.get("primary_tag")
        urgent = bool(payload.get("is_urgent"))
        urgency_prefix = "URGENT: " if urgent else ""
        subject = f"{urgency_prefix}{location_name} call alert ({_tag_label(primary_tag)})"

        caller_phone = payload.get("caller_phone_masked") or "Unknown"
        duration = format_duration(payload.get("duration_seconds"))
        tags = payload.get("tags") or []
        summary = payload.get("summary") or "No summary available."
        appointment_patient = payload.get("appointment_patient_redacted") or "Not provided"
        appointment_dt = payload.get("appointment_datetime") or "Not provided"
        appointment_provider = payload.get("appointment_provider") or "Not provided"
        appointment_service = payload.get("appointment_service") or "Not provided"
        html_tags = ", ".join(tags) if tags else "None"

        urgent_banner = (
            "<div style=\"padding:12px;background:#b91c1c;color:#fff;font-weight:700;border-radius:6px;"
            "margin-bottom:16px;\">URGENT: Emergency or complaint call requires immediate attention.</div>"
            if urgent
            else ""
        )

        html = (
            "<div style=\"font-family:Arial,sans-serif;line-height:1.45;color:#111;\">"
            f"{urgent_banner}"
            "<h2 style=\"margin:0 0 12px 0;\">New Call Created</h2>"
            "<table style=\"border-collapse:collapse;width:100%;max-width:680px;\">"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Caller Phone</td><td style=\"padding:6px 0;\">{escape(str(caller_phone))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Duration</td><td style=\"padding:6px 0;\">{escape(str(duration))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Primary Tag</td><td style=\"padding:6px 0;\">{escape(_tag_label(primary_tag))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">All Tags</td><td style=\"padding:6px 0;\">{escape(html_tags)}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Summary</td><td style=\"padding:6px 0;\">{escape(str(summary))}</td></tr>"
            "</table>"
            "<div style=\"margin-top:20px;padding:14px;border:2px solid #111;border-radius:8px;background:#f8fafc;\">"
            "<div style=\"font-size:18px;font-weight:800;margin-bottom:10px;\">Appointment Confirmation</div>"
            "<table style=\"border-collapse:collapse;width:100%;\">"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Patient</td><td style=\"padding:6px 0;\">{escape(str(appointment_patient))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Date/Time</td><td style=\"padding:6px 0;\">{escape(str(appointment_dt))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Provider</td><td style=\"padding:6px 0;\">{escape(str(appointment_provider))}</td></tr>"
            f"<tr><td style=\"padding:6px 0;font-weight:600;\">Service</td><td style=\"padding:6px 0;\">{escape(str(appointment_service))}</td></tr>"
            "</table>"
            "</div>"
            "</div>"
        )

        text = (
            f"{urgency_prefix}New Call Created\n\n"
            f"Caller Phone: {caller_phone}\n"
            f"Duration: {duration}\n"
            f"Primary Tag: {_tag_label(primary_tag)}\n"
            f"All Tags: {', '.join(tags) if tags else 'None'}\n"
            f"Summary: {summary}\n\n"
            "Appointment Confirmation\n"
            f"Patient: {appointment_patient}\n"
            f"Date/Time: {appointment_dt}\n"
            f"Provider: {appointment_provider}\n"
            f"Service: {appointment_service}\n"
        )

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
                logger.error("Resend send failed: status=%s body=%s", response.status_code, response.text[:500])
                response.raise_for_status()
