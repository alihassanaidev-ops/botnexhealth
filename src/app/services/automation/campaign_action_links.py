"""Signed, expiring per-run links for booking, confirming and rescheduling.

Campaign message wording has always supported ``{{booking_link}}``,
``{{confirmation_link}}`` and ``{{reschedule_link}}``, and templates already use
them — but nothing ever produced a value, so a message using one would reach a
patient with the link missing.

Design follows the unsubscribe token already in this codebase (HMAC over a
purpose-scoped payload, no raw identifier in the URL), with two additions a
patient action link needs and an unsubscribe link does not:

* **Scoped to one campaign run**, so a link can only ever act on the run it was
  issued for, and never on another patient's.
* **Expiring**, because a booking link that works forever is a permanent
  unauthenticated entry point to a clinic's schedule.

The action is inside the signed payload, so a confirmation link cannot be edited
into a reschedule link — purpose separation, the same reason an invite token must
not work for password reset.

Deliberately *not* single use. Magic-login guidance says burn the token on first
use, but a patient may well open a booking link, get interrupted, and come back to
it; a one-shot link would strand them. Expiry is the control here, not use count.

**Referer leakage.** These tokens travel in a URL that opens in the patient's
browser, which is how two in three hotel booking sites were found leaking guest
booking references to third parties: the token reaches any analytics or ad script
on the landing page through the ``Referer`` header. Every response serving one of
these links must send ``Referrer-Policy: no-referrer`` — see ``LINK_RESPONSE_HEADERS``.
"""

from __future__ import annotations

import hmac
import time
from typing import Literal

from src.app.services.sms_privacy import keyed_hash

_TOKEN_PURPOSE = "campaign-action-link-v1"
_SIG_LEN = 32  # hex chars

#: The actions a campaign message can link a patient to.
#:
#: ``cancel`` has no merge-field placeholder: it is reachable only from a link
#: the platform generates deliberately, not from wording a campaign author can
#: drop into a message by accident.
#: ``register`` converts a lead into a patient record. Like ``cancel`` it has no
#: merge-field placeholder: creating a record in a clinic's practice software is
#: deliberate, so the link is only ever issued by a ``patient_registration`` step,
#: never by wording a campaign author typed.
ACTIONS = ("book", "confirm", "reschedule", "cancel", "register")
Action = Literal["book", "confirm", "reschedule", "cancel", "register"]

#: Maps the merge-field placeholder to the action its link performs.
PLACEHOLDER_ACTIONS: dict[str, Action] = {
    "booking_link": "book",
    "confirmation_link": "confirm",
    "reschedule_link": "reschedule",
}

#: Long enough to outlive a campaign run — a reminder ladder can span a fortnight
#: — and short enough that an old message does not stay actionable forever.
DEFAULT_TTL_SECONDS = 14 * 24 * 60 * 60

#: Where a ``booking_link`` node records the rules its links must obey, and where
#: a ``patient_registration`` node records the provider a self-registered patient
#: is filed under. Both live on ``AutomationWorkflowRun.trigger_metadata`` so the
#: token stays a pure run reference: putting the configuration in the token would
#: make every link invalid the moment a campaign author edited the step, and would
#: hand the patient a payload they could read.
BOOKING_LINK_CONFIG_KEY = "booking_link_config"
REGISTRATION_CONFIG_KEY = "patient_registration_config"

#: Send these on any response that serves or redirects one of these links.
LINK_RESPONSE_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex, nofollow",
}


class LinkError(str):
    """Why a token was rejected. Distinguishes expiry from tampering."""


EXPIRED = "expired"
INVALID = "invalid"


def _signature(run_id: str, action: str, expires_at: int) -> str:
    return keyed_hash(
        f"{run_id}:{action}:{expires_at}",
        purpose=_TOKEN_PURPOSE,
        truncate_hex=_SIG_LEN,
    )


def make_action_token(
    run_id: str,
    action: Action,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> str:
    """Return a signed token authorising one action on one campaign run."""
    if action not in ACTIONS:
        raise ValueError(f"Unknown campaign link action: {action!r}")
    expires_at = int(now if now is not None else time.time()) + ttl_seconds
    return f"{run_id}.{action}.{expires_at}.{_signature(run_id, action, expires_at)}"


def verify_action_token(
    token: str | None, *, now: int | None = None
) -> tuple[str, Action] | str:
    """Return ``(run_id, action)`` when valid, else ``EXPIRED`` or ``INVALID``.

    Expiry and tampering are returned separately so the page can tell a patient
    their link has run out — which they can act on — rather than showing the same
    blank refusal it shows a forged one.
    """
    if not token:
        return INVALID
    parts = token.split(".")
    if len(parts) != 4:
        return INVALID
    run_id, action, raw_expiry, sig = parts
    if not run_id or action not in ACTIONS or not sig:
        return INVALID
    try:
        expires_at = int(raw_expiry)
    except ValueError:
        return INVALID

    # Verify the signature before trusting the expiry it covers: an unsigned
    # expiry could simply be edited forward.
    if not hmac.compare_digest(_signature(run_id, action, expires_at), sig):
        return INVALID
    if (now if now is not None else int(time.time())) >= expires_at:
        return EXPIRED
    return run_id, action  # type: ignore[return-value]


def action_url(base_url: str, token: str, action: Action) -> str:
    return f"{base_url.rstrip('/')}/api/campaigns/link/{action}?token={token}"


def build_run_links(
    run_id: str,
    base_url: str,
    *,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> dict[str, str]:
    """Build all three links for a run, keyed by their merge-field placeholder."""
    return {
        placeholder: action_url(
            base_url,
            make_action_token(run_id, action, ttl_seconds=ttl_seconds, now=now),
            action,
        )
        for placeholder, action in PLACEHOLDER_ACTIONS.items()
    }
