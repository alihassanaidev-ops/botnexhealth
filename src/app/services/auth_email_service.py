"""Auth email sender for invite and password reset flows."""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from src.app.config import settings

logger = logging.getLogger(__name__)


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
            subject="Set up your NexHealth account",
            html=(
                "<p>You were invited to NexHealth.</p>"
                f"<p><a href=\"{link}\">Set your password</a></p>"
            ),
            text=(
                "You were invited to NexHealth.\n\n"
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
            subject="Reset your NexHealth password",
            html=(
                "<p>You requested a password reset.</p>"
                f"<p><a href=\"{link}\">Reset your password</a></p>"
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
                logger.error(
                    "Auth email send failed: status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                response.raise_for_status()
