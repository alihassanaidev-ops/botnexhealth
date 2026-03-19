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
            '<tr><td style="padding:0 0 16px;">'
            '<div style="padding:12px 16px;background:#991b1b;color:#fef2f2;font-weight:600;'
            'border-radius:8px;font-size:13px;text-align:center;">'
            "URGENT: Emergency or complaint call requires immediate attention.</div>"
            "</td></tr>"
            if urgent
            else ""
        )

        row_style = "padding:10px 0;border-bottom:1px solid #27272a;font-size:14px;"
        label_style = f"{row_style}color:#a1a1aa;width:120px;vertical-align:top;"
        value_style = f"{row_style}color:#e4e4e7;"

        html = (
            '<!DOCTYPE html><html><head><meta charset="UTF-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1.0">'
            "</head>"
            '<body style="margin:0;padding:0;background-color:#09090b;'
            "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;\">"
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' style="background-color:#09090b;padding:40px 20px;"><tr><td align="center">'
            '<table role="presentation" width="520" cellpadding="0" cellspacing="0"'
            ' style="max-width:520px;width:100%;">'
            # Brand
            '<tr><td align="center" style="padding-bottom:32px;">'
            '<span style="font-size:24px;font-weight:700;color:#ffffff;letter-spacing:-0.5px;">'
            f"{escape(location_name)}</span></td></tr>"
            # Card
            '<tr><td style="background-color:#18181b;border:1px solid #27272a;'
            'border-radius:12px;padding:40px 36px;">'
            # Urgent banner
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{urgent_banner}</table>'
            # Heading
            '<h2 style="margin:0 0 4px;font-size:20px;font-weight:600;color:#fafafa;">New Call Alert</h2>'
            '<p style="margin:0 0 24px;font-size:13px;color:#71717a;">A new call has been processed and classified.</p>'
            # Call details
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' style="border-collapse:collapse;">'
            f'<tr><td style="{label_style}">Caller</td>'
            f'<td style="{value_style}font-family:monospace;">{escape(str(caller_phone))}</td></tr>'
            f'<tr><td style="{label_style}">Duration</td>'
            f'<td style="{value_style}">{escape(str(duration))}</td></tr>'
            f'<tr><td style="{label_style}">Primary Tag</td>'
            f'<td style="{value_style}">'
            f'<span style="display:inline-block;background:#7c3aed;color:#fff;padding:2px 10px;'
            f'border-radius:12px;font-size:12px;font-weight:600;">{escape(_tag_label(primary_tag))}</span></td></tr>'
            f'<tr><td style="{label_style}">All Tags</td>'
            f'<td style="{value_style}font-size:13px;">{escape(html_tags)}</td></tr>'
            f'<tr><td style="{label_style}">Summary</td>'
            f'<td style="{value_style}font-size:13px;line-height:1.5;">{escape(str(summary))}</td></tr>'
            "</table>"
            # Appointment section
            '<div style="margin-top:24px;padding:20px;background:#09090b;border:1px solid #27272a;'
            'border-radius:8px;">'
            '<div style="font-size:14px;font-weight:600;color:#fafafa;margin-bottom:14px;">'
            "Appointment Confirmation</div>"
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0"'
            ' style="border-collapse:collapse;">'
            f'<tr><td style="{label_style}">Patient</td>'
            f'<td style="{value_style}">{escape(str(appointment_patient))}</td></tr>'
            f'<tr><td style="{label_style}">Date/Time</td>'
            f'<td style="{value_style}">{escape(str(appointment_dt))}</td></tr>'
            f'<tr><td style="{label_style}">Provider</td>'
            f'<td style="{value_style}">{escape(str(appointment_provider))}</td></tr>'
            f'<tr><td style="{label_style}border-bottom:none;">'
            f"Service</td>"
            f'<td style="{value_style}border-bottom:none;">'
            f"{escape(str(appointment_service))}</td></tr>"
            "</table></div>"
            # End card
            "</td></tr>"
            # Footer
            '<tr><td align="center" style="padding-top:28px;">'
            '<p style="margin:0;font-size:12px;color:#3f3f46;">'
            f"&copy; {escape(location_name)}</p>"
            "</td></tr>"
            "</table></td></tr></table></body></html>"
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
