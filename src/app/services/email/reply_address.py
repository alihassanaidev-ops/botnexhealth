"""Signed reply addresses.

Every patient-facing email carries a Reply-To whose local part encodes the
conversation it belongs to::

    r+<institution>.<location>.<contact>.<run>.<signature>@inbound.example.com

Inbound mail is received on a catch-all basis — one MX record for the whole
domain, not one address per clinic — so the address itself has to carry the
routing. Providers cap inbound routing rules (SES allows 200 per rule set and
will not raise it), which a rule-per-clinic design would exhaust; a catch-all
plus a signed token stays at one rule no matter how many clinics exist.

The signature is what makes that safe. Without it anyone could post mail to a
made-up address and have it filed into another clinic's inbox. Ids are
abbreviated to keep the local part inside the 64-character limit RFC 5321 sets,
and resolution treats them as prefixes.

The token identifies a **conversation**, never a person. A reply can arrive from
a forwarded copy, a shared family mailbox or an assistant, so the sender is
verified separately and a mismatch is flagged rather than assumed away.
"""

from __future__ import annotations

import hmac
import re

from src.app.services.sms_privacy import keyed_hash

_TOKEN_PURPOSE = "email-reply-address-token-v1"

# Local-part budget, which RFC 5321 caps at 64 octets:
#
#   "r+"            2
#   4 ids          40   (4 x _ID_LEN)
#   4 separators    4
#   signature      12   (_SIG_LEN)
#   ————————————————
#                  58
#
# Ids are prefixes, not full UUIDs, and resolution is a prefix match scoped to
# one institution — 40 bits is far more than enough to be unambiguous at clinic
# scale. The signature keeps 48 bits, and forging it costs one *delivered email*
# per attempt, so the work factor is not the binding constraint here.
_SIG_LEN = 12
_ID_LEN = 10

#: r+<inst>.<loc>.<contact>.<run>.<sig>
_TOKEN_RE = re.compile(
    r"^r\+([0-9a-f]{1,32})\.([0-9a-f]{0,32})\.([0-9a-f]{0,32})\.([0-9a-f]{0,32})\.([0-9a-f]{%d})$"
    % _SIG_LEN
)

#: RFC 5321 caps a local part at 64 octets.
MAX_LOCAL_PART = 64


def _short(value: str | None) -> str:
    """Hex-only prefix of an id, safe for an address local part.

    Non-hex characters are dropped rather than passed through. Ids are UUIDs in
    practice, but a token built from anything else would be generated happily
    and then fail to parse on the way back in — a silent, one-way failure that
    would look like the patient never replied.
    """
    if not value:
        return ""
    cleaned = "".join(c for c in str(value).lower() if c in "0123456789abcdef")
    return cleaned[:_ID_LEN]


def _signature(institution: str, location: str, contact: str, run: str) -> str:
    return keyed_hash(
        f"{institution}:{location}:{contact}:{run}",
        purpose=_TOKEN_PURPOSE,
        truncate_hex=_SIG_LEN,
    )


def make_reply_token(
    *,
    institution_id: str,
    location_id: str | None = None,
    contact_id: str | None = None,
    workflow_run_id: str | None = None,
) -> str:
    """Build the signed local part for a conversation."""
    inst = _short(institution_id)
    loc = _short(location_id)
    contact = _short(contact_id)
    run = _short(workflow_run_id)
    signature = _signature(inst, loc, contact, run)
    return f"r+{inst}.{loc}.{contact}.{run}.{signature}"


def make_reply_address(
    domain: str,
    *,
    institution_id: str,
    location_id: str | None = None,
    contact_id: str | None = None,
    workflow_run_id: str | None = None,
) -> str:
    token = make_reply_token(
        institution_id=institution_id,
        location_id=location_id,
        contact_id=contact_id,
        workflow_run_id=workflow_run_id,
    )
    if len(token) > MAX_LOCAL_PART:  # pragma: no cover — ids are fixed width
        raise ValueError("Reply address local part exceeds the RFC 5321 limit")
    return f"{token}@{domain}"


class ReplyRoute:
    """The conversation a verified token points at.

    Values are **id prefixes**, not full ids: resolution is a prefix match
    scoped to the institution.
    """

    __slots__ = ("institution_prefix", "location_prefix", "contact_prefix", "run_prefix")

    def __init__(
        self,
        institution_prefix: str,
        location_prefix: str,
        contact_prefix: str,
        run_prefix: str,
    ) -> None:
        self.institution_prefix = institution_prefix
        self.location_prefix = location_prefix or ""
        self.contact_prefix = contact_prefix or ""
        self.run_prefix = run_prefix or ""

    def __repr__(self) -> str:
        return (
            f"<ReplyRoute institution={self.institution_prefix} "
            f"contact={self.contact_prefix} run={self.run_prefix}>"
        )

    def __eq__(self, other: object) -> bool:
        return isinstance(other, ReplyRoute) and (
            self.institution_prefix,
            self.location_prefix,
            self.contact_prefix,
            self.run_prefix,
        ) == (
            other.institution_prefix,
            other.location_prefix,
            other.contact_prefix,
            other.run_prefix,
        )


def parse_reply_address(address: str | None) -> ReplyRoute | None:
    """Verify a reply address and return what it points at, or None.

    Returns None for anything that does not verify — a forged address, a typo,
    or ordinary mail sent to the domain by someone who was never emailed. The
    caller quarantines rather than guessing, because guessing a tenant is a
    cross-tenant disclosure.
    """
    if not address:
        return None
    local_part = address.split("@", 1)[0].strip().lower()
    match = _TOKEN_RE.match(local_part)
    if not match:
        return None

    inst, loc, contact, run, signature = match.groups()
    expected = _signature(inst, loc, contact, run)
    if not hmac.compare_digest(expected, signature):
        return None
    return ReplyRoute(inst, loc, contact, run)


def find_reply_address(candidates: list[str] | None) -> str | None:
    """Pick the routable address out of a recipient list.

    Inbound arrives on a catch-all, so a message may be addressed to several
    recipients of which only one is ours.
    """
    for candidate in candidates or []:
        if parse_reply_address(candidate) is not None:
            return candidate
    return None
