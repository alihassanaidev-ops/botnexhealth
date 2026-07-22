"""Auth email sender for invite and password reset flows."""

from __future__ import annotations

import logging
from string import Template
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from src.app.config import settings

logger = logging.getLogger(__name__)


# Branded HTML wrapper for transactional auth emails. Mirrors the web app's
# dark-mode-first look: near-black page, #0d0d0d card with a subtle border,
# the purple->blue primary gradient on the action button, and the
# "ScaleNexus.AI" wordmark. Uses table layout + inline styles for email-client
# compatibility, with a solid-purple bgcolor fallback for clients (Outlook)
# that ignore CSS gradients. string.Template ($-substitution) is used instead
# of an f-string/.format so the CSS braces don't need escaping.
_BRANDED_EMAIL_TEMPLATE = Template(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<meta name="color-scheme" content="dark">
</head>
<body style="margin:0;padding:0;background-color:#050505;">
<span style="display:none;max-height:0;overflow:hidden;opacity:0;color:#050505;">$preheader</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color:#050505;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" style="width:480px;max-width:100%;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;">
<tr><td style="padding:0 4px 20px;">
<span style="font-size:18px;font-weight:600;letter-spacing:-0.02em;color:#fafafa;">ScaleNexus<span style="color:#a78bfa;">.AI</span></span>
</td></tr>
<tr><td style="background-color:#0d0d0d;border:1px solid #1f2937;border-radius:12px;padding:32px;">
<h1 style="margin:0 0 12px;font-size:20px;font-weight:600;letter-spacing:-0.02em;color:#eef2ff;">$heading</h1>
<p style="margin:0 0 24px;font-size:14px;line-height:22px;color:#94a3b8;">$intro</p>
<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
<td align="center" bgcolor="#8338ec" style="border-radius:8px;background-color:#8338ec;background-image:linear-gradient(90deg,#8338ec,#3b82f6);">
<a href="$link" style="display:inline-block;padding:12px 28px;font-size:14px;font-weight:600;line-height:20px;color:#ffffff;text-decoration:none;border-radius:8px;">$button_label</a>
</td></tr></table>
<p style="margin:24px 0 0;font-size:12px;line-height:18px;color:#6b7280;">Or paste this link into your browser:<br><a href="$link" style="color:#a78bfa;text-decoration:none;word-break:break-all;">$link</a></p>
</td></tr>
<tr><td style="padding:20px 4px 0;">
<p style="margin:0;font-size:12px;line-height:18px;color:#6b7280;">$footer</p>
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>"""
)


class AuthEmailService:
    """Send invite and password reset emails via Resend."""

    async def send_invite_email(
        self,
        *,
        email: str,
        token: str,
        redirect_url: str | None = None,
    ) -> None:
        link = self.build_action_url(
            token=token,
            flow="invite",
            redirect_url=redirect_url,
            default_path="/set-password",
        )
        await self._send_email(
            to=email,
            subject="Set up your ScaleNexus account",
            html=self._render_branded_html(
                preheader="Set your password to activate your ScaleNexus account.",
                heading="Set up your account",
                intro=(
                    "You've been invited to ScaleNexus. Click the button below to "
                    "set your password and get started."
                ),
                button_label="Set your password",
                link=link,
                footer=(
                    "If you weren't expecting this invitation, you can safely "
                    "ignore this email."
                ),
            ),
            text=(
                "You were invited to ScaleNexus.\n\n"
                f"Set your password: {link}\n"
            ),
        )

    async def send_password_reset_email(
        self,
        *,
        email: str,
        token: str,
        redirect_url: str | None = None,
    ) -> None:
        link = self.build_action_url(
            token=token,
            flow="reset",
            redirect_url=redirect_url,
            default_path="/set-password",
        )
        await self._send_email(
            to=email,
            subject="Reset your ScaleNexus password",
            html=self._render_branded_html(
                preheader="Reset your ScaleNexus password.",
                heading="Reset your password",
                intro=(
                    "We received a request to reset your ScaleNexus password. "
                    "Click the button below to choose a new one."
                ),
                button_label="Reset password",
                link=link,
                footer=(
                    "If you didn't request this, you can safely ignore this email "
                    "— your password won't change."
                ),
            ),
            text=(
                "You requested a password reset.\n\n"
                f"Reset your password: {link}\n"
            ),
        )

    def build_action_url(
        self,
        *,
        token: str,
        flow: str,
        redirect_url: str | None,
        default_path: str,
    ) -> str:
        """Build an absolute frontend URL with auth query params."""
        base_url = self.resolve_redirect_url(
            redirect_url=redirect_url,
            default_path=default_path,
        )
        parsed = urlsplit(base_url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        query["token"] = token
        query["flow"] = flow
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                parsed.path,
                urlencode(query),
                parsed.fragment,
            )
        )

    def resolve_redirect_url(
        self,
        *,
        redirect_url: str | None,
        default_path: str,
    ) -> str:
        if redirect_url:
            redirect_url = redirect_url.strip()
            if any(char in redirect_url for char in ("\r", "\n", "\t")):
                raise ValueError("Redirect URL is invalid")
            if redirect_url.startswith("//"):
                raise ValueError("Redirect URL host is not allowed")

            parsed = urlsplit(redirect_url)
            if parsed.scheme or parsed.netloc:
                return self._validate_absolute_redirect(parsed)

            if settings.auth_frontend_base_url:
                absolute_url = urljoin(
                    settings.auth_frontend_base_url.rstrip("/") + "/",
                    redirect_url.lstrip("/"),
                )
                return self._validate_absolute_redirect(urlsplit(absolute_url))

            raise RuntimeError("AUTH_FRONTEND_BASE_URL is required for relative redirect URLs")

        if not settings.auth_frontend_base_url:
            raise RuntimeError("AUTH_FRONTEND_BASE_URL is not configured")

        absolute_url = urljoin(
            settings.auth_frontend_base_url.rstrip("/") + "/",
            default_path.lstrip("/"),
        )
        return self._validate_absolute_redirect(urlsplit(absolute_url))

    def _validate_absolute_redirect(self, parsed: Any) -> str:
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Redirect URL must use http or https")
        if not parsed.netloc:
            raise ValueError("Redirect URL must include a host")
        if parsed.username or parsed.password:
            raise ValueError("Redirect URL credentials are not allowed")
        if settings.is_production and parsed.scheme != "https":
            raise ValueError("Redirect URL must use https")
        if parsed.netloc.lower() not in settings.allowed_auth_redirect_netlocs:
            raise ValueError("Redirect URL host is not allowed")

        return urlunsplit(
            (parsed.scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment)
        )

    def _render_branded_html(
        self,
        *,
        preheader: str,
        heading: str,
        intro: str,
        button_label: str,
        link: str,
        footer: str,
    ) -> str:
        """Render the branded auth-email HTML matching the web app's look."""
        return _BRANDED_EMAIL_TEMPLATE.substitute(
            preheader=preheader,
            heading=heading,
            intro=intro,
            button_label=button_label,
            link=link,
            footer=footer,
        )

    async def _send_email(
        self,
        *,
        to: str,
        subject: str,
        html: str,
        text: str,
    ) -> None:
        api_key = settings.resend_api_key
        sender = settings.resend_from_email
        if not api_key or not sender:
            raise RuntimeError("Resend is not configured (RESEND_API_KEY / RESEND_FROM_EMAIL)")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "from": sender,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        if settings.resend_reply_to:
            payload["reply_to"] = settings.resend_reply_to

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(
                "https://api.resend.com/emails",
                headers=headers,
                json=payload,
            )
            if response.status_code >= 400:
                # Never log the response body. The request body contains a
                # live action URL with ?token=...; if the provider or any
                # proxy in front of it echoes request content into the
                # error response, logging response.text leaks the token —
                # which is effectively a credential. Log only safe fields
                # plus the provider's request id for triage.
                logger.error(
                    "Auth email send failed: provider=resend status=%s "
                    "body_bytes=%d request_id=%s",
                    response.status_code,
                    len(response.content or b""),
                    response.headers.get("x-request-id") or "-",
                )
                response.raise_for_status()
