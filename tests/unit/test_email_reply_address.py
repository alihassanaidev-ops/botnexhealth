"""Unit tests for signed reply addresses.

The signature is the security boundary. Inbound mail arrives on a catch-all, so
anything that parses is filed into a clinic's inbox — an unsigned or forgeable
token would let anyone post mail into another clinic's conversation.
"""

from __future__ import annotations

import pytest

from src.app.services.email.reply_address import (
    MAX_LOCAL_PART,
    ReplyRoute,
    find_reply_address,
    make_reply_address,
    make_reply_token,
    parse_reply_address,
)

INST = "11111111-2222-3333-4444-555555555555"
LOC = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CONTACT = "99999999-8888-7777-6666-555555555555"
RUN = "12341234-5678-5678-9abc-9abc9abc9abc"


def _token(**kw):
    base = dict(
        institution_id=INST, location_id=LOC, contact_id=CONTACT, workflow_run_id=RUN
    )
    base.update(kw)
    return make_reply_token(**base)


# ---------------------------------------------------------------------------
# Round trip
# ---------------------------------------------------------------------------


def test_token_round_trips():
    route = parse_reply_address(f"{_token()}@inbound.example.com")
    assert route is not None
    assert INST.replace("-", "").startswith(route.institution_prefix)
    assert CONTACT.replace("-", "").startswith(route.contact_prefix)
    assert RUN.replace("-", "").startswith(route.run_prefix)


def test_address_is_built_on_the_given_domain():
    address = make_reply_address(
        "inbound.example.com", institution_id=INST, contact_id=CONTACT
    )
    assert address.endswith("@inbound.example.com")
    assert parse_reply_address(address) is not None


def test_optional_parts_may_be_omitted():
    """A reply can be about a clinic without belonging to a run — for example a
    reply to a one-off staff message."""
    route = parse_reply_address(f"{_token(location_id=None, workflow_run_id=None)}@x.io")
    assert route is not None
    assert route.location_prefix == ""
    assert route.run_prefix == ""


def test_local_part_stays_within_the_rfc_limit():
    """RFC 5321 caps a local part at 64 octets; an over-long address is silently
    unroutable at some providers."""
    assert len(_token()) <= MAX_LOCAL_PART


def test_parsing_is_case_insensitive():
    """Mail servers may normalise the local part's case in transit."""
    address = f"{_token()}@inbound.example.com"
    assert parse_reply_address(address.upper()) is not None


# ---------------------------------------------------------------------------
# Forgery and malformed input
# ---------------------------------------------------------------------------


def test_tampered_signature_is_rejected():
    token = _token()
    forged = token[:-1] + ("0" if token[-1] != "0" else "1")
    assert parse_reply_address(f"{forged}@inbound.example.com") is None


def test_tampered_institution_is_rejected():
    """The whole point: swapping the institution must not survive the signature."""
    token = _token()
    parts = token.split(".")
    parts[0] = "r+ffffffffffff"
    assert parse_reply_address(".".join(parts) + "@inbound.example.com") is None


def test_tampered_contact_is_rejected():
    parts = _token().split(".")
    parts[2] = "ffffffffffff"
    assert parse_reply_address(".".join(parts) + "@inbound.example.com") is None


@pytest.mark.parametrize(
    "address",
    [
        None,
        "",
        "hello@inbound.example.com",
        "r+notavalidtoken@inbound.example.com",
        "r+aaa.bbb.ccc@inbound.example.com",          # too few segments
        "r+aaa.bbb.ccc.ddd.eee.fff@inbound.example.com",  # too many
        "support@clinic.com",
        "r+@inbound.example.com",
        "r+....@inbound.example.com",
    ],
)
def test_non_routable_addresses_return_none(address):
    """Ordinary mail to the catch-all must not resolve to a tenant."""
    assert parse_reply_address(address) is None


def test_signature_is_bound_to_all_parts():
    """A signature from one conversation must not validate another."""
    a = _token()
    b = _token(contact_id="deadbeefdeadbeefdeadbeefdeadbeef")
    sig_a = a.rsplit(".", 1)[1]
    spliced = b.rsplit(".", 1)[0] + "." + sig_a
    assert parse_reply_address(f"{spliced}@inbound.example.com") is None


# ---------------------------------------------------------------------------
# Recipient selection
# ---------------------------------------------------------------------------


def test_find_reply_address_picks_ours_from_a_recipient_list():
    """A message may be addressed to several people; only one is ours."""
    ours = f"{_token()}@inbound.example.com"
    found = find_reply_address(["someone@elsewhere.com", ours, "cc@other.com"])
    assert found == ours


def test_find_reply_address_returns_none_when_absent():
    assert find_reply_address(["a@b.com", "c@d.com"]) is None
    assert find_reply_address([]) is None
    assert find_reply_address(None) is None


def test_reply_route_equality():
    assert ReplyRoute("a", "b", "c", "d") == ReplyRoute("a", "b", "c", "d")
    assert ReplyRoute("a", "b", "c", "d") != ReplyRoute("a", "b", "c", "e")
