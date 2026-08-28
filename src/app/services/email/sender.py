"""Provider-agnostic outbound email.

One seam with two implementations, so which provider carries a message is a
configuration decision rather than something hardcoded at each call site.

The split exists for a compliance reason, not a technical one. Auth emails and
staff call alerts carry no patient health information and stay on Resend, which
already sends them. Patient-facing campaign mail can move to Amazon SES, which
is HIPAA-eligible under the AWS agreement and — in ``ca-central-1`` — keeps the
content in the same account and region as the rest of the platform.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from src.app.config import settings

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


class EmailSendError(RuntimeError):
    """The provider refused or failed to accept the message."""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        #: Throttling and 5xx are worth retrying; a rejected address is not.
        self.retryable = retryable


@dataclass(frozen=True)
class EmailMessage:
    from_address: str
    to: list[str]
    subject: str
    text: str
    from_name: str | None = None
    html: str | None = None
    reply_to: str | None = None
    #: Stable per (run, node). Lets a retry after a crash dedupe at the provider
    #: rather than emailing the patient twice.
    idempotency_key: str | None = None
    #: Scopes bounce/complaint handling back to the owning clinic.
    institution_id: str | None = None
    #: SES only — the tenant and configuration set bound to this clinic.
    tenant_name: str | None = None
    configuration_set: str | None = None
    tags: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EmailSendResult:
    provider: str
    provider_message_id: str | None


def _formatted_from(message: EmailMessage) -> str:
    if message.from_name:
        return f"{message.from_name} <{message.from_address}>"
    return message.from_address


class EmailSender(Protocol):
    provider: str

    async def send(self, message: EmailMessage) -> EmailSendResult: ...


class ResendSender:
    """Sends through Resend's HTTP API."""

    provider = "resend"

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key or settings.resend_api_key

    async def send(self, message: EmailMessage) -> EmailSendResult:
        if not self._api_key:
            raise EmailSendError("Resend is not configured (RESEND_API_KEY)", retryable=False)

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if message.idempotency_key:
            headers["Idempotency-Key"] = message.idempotency_key

        payload: dict = {
            "from": _formatted_from(message),
            "to": list(message.to),
            "subject": message.subject,
            "text": message.text,
        }
        if message.html:
            payload["html"] = message.html
        if message.reply_to:
            payload["reply_to"] = message.reply_to

        tags = dict(message.tags)
        if message.institution_id:
            tags.setdefault("institution_id", str(message.institution_id))
        if tags:
            payload["tags"] = [{"name": k, "value": v} for k, v in tags.items()]

        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(_RESEND_URL, headers=headers, json=payload)

        if response.status_code >= 400:
            raise EmailSendError(
                f"Resend returned {response.status_code}: {response.text[:200]}",
                # 4xx other than rate-limiting is a rejected message, not a blip.
                retryable=response.status_code == 429 or response.status_code >= 500,
            )

        try:
            provider_id = (response.json() or {}).get("id")
        except Exception:  # noqa: BLE001 — body may not be JSON
            provider_id = None
        return EmailSendResult(provider=self.provider, provider_message_id=provider_id)


class SesSender:
    """Sends through Amazon SES v2.

    boto3 is synchronous, so the call runs in a worker thread rather than
    blocking the event loop while the dispatcher holds a row lock.
    """

    provider = "ses"

    #: SES error codes that mean "try again", as opposed to a rejected message.
    _RETRYABLE_CODES = frozenset(
        {
            "TooManyRequestsException",
            "ThrottlingException",
            "ServiceUnavailable",
            "InternalServiceErrorException",
            "LimitExceededException",
        }
    )

    def __init__(self, region: str | None = None, client=None) -> None:  # noqa: ANN001
        self._region = region or settings.ses_region
        self._client = client

    def _get_client(self):  # noqa: ANN202
        if self._client is None:
            import boto3

            # Credentials come from the environment: the ECS task role in
            # production, the configured profile locally. Never from config.
            self._client = boto3.client("sesv2", region_name=self._region)
        return self._client

    async def send(self, message: EmailMessage) -> EmailSendResult:
        return await asyncio.to_thread(self._send_sync, message)

    def _send_sync(self, message: EmailMessage) -> EmailSendResult:
        from botocore.exceptions import BotoCoreError, ClientError

        body: dict = {"Text": {"Data": message.text, "Charset": "UTF-8"}}
        if message.html:
            body["Html"] = {"Data": message.html, "Charset": "UTF-8"}

        request: dict = {
            "FromEmailAddress": _formatted_from(message),
            "Destination": {"ToAddresses": list(message.to)},
            "Content": {
                "Simple": {
                    "Subject": {"Data": message.subject, "Charset": "UTF-8"},
                    "Body": body,
                }
            },
        }
        if message.reply_to:
            request["ReplyToAddresses"] = [message.reply_to]
        if message.configuration_set:
            request["ConfigurationSetName"] = message.configuration_set
        if message.tenant_name:
            # Scopes reputation and suppression to this clinic, so one clinic's
            # bounce rate cannot pause everyone else's sending.
            request["TenantName"] = message.tenant_name

        tags = dict(message.tags)
        if message.institution_id:
            tags.setdefault("institution_id", str(message.institution_id))
        if tags:
            # SES tag values are restricted to alphanumerics, hyphen and
            # underscore, so anything else is dropped rather than rejected.
            request["EmailTags"] = [
                {"Name": k, "Value": _safe_tag_value(v)} for k, v in tags.items()
            ]

        try:
            response = self._get_client().send_email(**request)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            raise EmailSendError(
                f"SES rejected the message ({code})",
                retryable=code in self._RETRYABLE_CODES,
            ) from exc
        except BotoCoreError as exc:
            raise EmailSendError(f"SES transport error: {type(exc).__name__}") from exc

        return EmailSendResult(
            provider=self.provider, provider_message_id=response.get("MessageId")
        )


def _safe_tag_value(value: str) -> str:
    cleaned = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(value))
    return cleaned[:256] or "unknown"


def get_patient_email_sender() -> EmailSender:
    """The sender for patient-facing campaign mail."""
    if settings.patient_email_provider == "ses":
        return SesSender()
    return ResendSender()


def get_transactional_email_sender() -> EmailSender:
    """The sender for auth mail and staff alerts — always Resend.

    These carry no patient health information and are already branded and
    hardened there, so moving them would be churn without benefit.
    """
    return ResendSender()
