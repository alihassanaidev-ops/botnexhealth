"""Provision Twilio phone numbers for this deployment's inbound SMS webhook."""

from __future__ import annotations

from dataclasses import dataclass


class TwilioPhoneNumberNotFoundError(ValueError):
    """The selected number is not owned by the configured Twilio account."""


class TwilioPhoneNumberSmsUnsupportedError(ValueError):
    """The selected Twilio number cannot receive SMS messages."""


class TwilioSmsApplicationConflictError(RuntimeError):
    """A Twilio SMS Application currently controls the selected number."""


@dataclass(frozen=True)
class TwilioWebhookConfigurationResult:
    phone_number_sid: str
    webhook_url: str
    changed: bool


def configure_inbound_sms_webhook(
    *,
    account_sid: str,
    auth_token: str,
    phone_number: str,
    webhook_url: str,
) -> TwilioWebhookConfigurationResult:
    """Point one account-owned phone number at the platform inbound SMS route.

    Selecting a number in Super Admin is the explicit ownership action, so an
    existing direct ``sms_url`` is replaced. A bound SMS Application is not
    cleared automatically because doing so could disconnect another product.
    """
    from twilio.rest import Client

    client = Client(account_sid, auth_token)
    numbers = client.incoming_phone_numbers.list(
        phone_number=phone_number,
        limit=2,
    )
    exact_matches = [
        number
        for number in numbers
        if (getattr(number, "phone_number", "") or "").strip() == phone_number
    ]
    if len(exact_matches) != 1:
        raise TwilioPhoneNumberNotFoundError(
            "The selected number was not found in this institution's Twilio account"
        )

    number = exact_matches[0]
    capabilities = getattr(number, "capabilities", None) or {}
    if not bool(capabilities.get("sms", False)):
        raise TwilioPhoneNumberSmsUnsupportedError(
            "The selected Twilio number is not SMS-capable"
        )

    if getattr(number, "sms_application_sid", None):
        raise TwilioSmsApplicationConflictError(
            "The selected number is controlled by a Twilio SMS Application. "
            "Remove that binding before assigning it to this location."
        )

    current_url = (getattr(number, "sms_url", None) or "").rstrip("/")
    current_method = (getattr(number, "sms_method", None) or "").upper()
    normalized_url = webhook_url.rstrip("/")
    changed = current_url != normalized_url or current_method != "POST"
    if changed:
        client.incoming_phone_numbers(number.sid).update(
            sms_url=normalized_url,
            sms_method="POST",
        )

    return TwilioWebhookConfigurationResult(
        phone_number_sid=number.sid,
        webhook_url=normalized_url,
        changed=changed,
    )
