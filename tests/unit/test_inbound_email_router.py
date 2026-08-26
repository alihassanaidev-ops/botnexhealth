"""Unit tests for inbound email routing decisions.

The ordering of the guards is the security property here: hostile content is
rejected before it can reach a clinic, a message that cannot be attributed is
never guessed at, and opt-out is honoured before anything else happens to the
message.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.models.inbound_email_message import InboundEmailIntent, InboundEmailStatus
from src.app.services.email.inbound_parser import ParsedEmail
from src.app.services.email.inbound_router import (
    InboundEmailRouter,
    InboundVerdicts,
    _addresses_differ,
    _mask_email,
)
from src.app.services.email.reply_address import make_reply_address

INST_ID = "11111111-2222-3333-4444-555555555555"
CONTACT_ID = "99999999-8888-7777-6666-555555555555"
INBOUND_DOMAIN = "inbound.example.com"


def _reply_to(**kw) -> str:
    base = dict(institution_id=INST_ID, contact_id=CONTACT_ID, location_id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    base.update(kw)
    return make_reply_address(INBOUND_DOMAIN, **base)


def _parsed(**kw) -> ParsedEmail:
    base = dict(
        from_address="patient@example.com",
        to_addresses=[_reply_to()],
        subject="Re: Your appointment",
        body_text="Yes, that works.",
        message_id="<m1@example.com>",
    )
    base.update(kw)
    return ParsedEmail(**base)


def _institution():
    inst = MagicMock()
    inst.id = INST_ID
    return inst


def _contact(email="patient@example.com", location_id="loc-1"):
    contact = MagicMock()
    contact.id = CONTACT_ID
    contact.email = email
    contact.location_id = location_id
    return contact


def _router(*, institution=None, contact=None, thread=None, seen=False, over_limit=False):
    session = AsyncMock()
    router = InboundEmailRouter(session)
    router._already_seen = AsyncMock(return_value=seen)
    router._sender_over_limit = AsyncMock(return_value=over_limit)
    router._resolve_institution = AsyncMock(return_value=institution)
    router._resolve_contact = AsyncMock(return_value=contact)
    router._attach_thread = AsyncMock(return_value=thread)
    return router


def _route(router, parsed=None, **kw):
    with patch("src.app.services.email.inbound_router.settings") as s:
        s.inbound_email_max_body_bytes = 256_000
        s.inbound_email_sender_hourly_limit = 60
        return asyncio.run(router.route(parsed or _parsed(), **kw))


# ---------------------------------------------------------------------------
# Guard ordering
# ---------------------------------------------------------------------------


def test_spam_is_quarantined_and_never_routed():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, verdicts=InboundVerdicts(spam="FAIL"))

    assert result.message.status == InboundEmailStatus.QUARANTINED.value
    assert result.thread is None
    router._resolve_institution.assert_not_awaited()


def test_malware_is_quarantined():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, verdicts=InboundVerdicts(virus="FAIL"))
    assert result.message.status == InboundEmailStatus.QUARANTINED.value


def test_spf_failure_alone_does_not_quarantine():
    """Legitimate forwarded mail routinely fails SPF; treating that as hostile
    would drop real patient replies."""
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, verdicts=InboundVerdicts(spf="FAIL", dkim="FAIL"))
    assert result.message.status == InboundEmailStatus.ROUTED.value


def test_unsigned_address_is_unroutable_not_guessed():
    """Filing a message into the wrong clinic is a cross-tenant disclosure —
    worse than not routing it at all."""
    router = _router(institution=_institution())
    parsed = _parsed(to_addresses=["support@inbound.example.com"])
    result = _route(router, parsed)

    assert result.message.status == InboundEmailStatus.UNROUTABLE.value
    assert result.message.institution_id is None
    router._resolve_institution.assert_not_awaited()


def test_forged_token_is_unroutable():
    forged = _reply_to().replace("@", "x@", 1)
    router = _router(institution=_institution())
    result = _route(router, _parsed(to_addresses=[forged]))
    assert result.message.status == InboundEmailStatus.UNROUTABLE.value


def test_unknown_institution_is_unroutable():
    router = _router(institution=None)
    result = _route(router)
    assert result.message.status == InboundEmailStatus.UNROUTABLE.value


def test_duplicate_delivery_is_ignored():
    router = _router(institution=_institution(), seen=True)
    assert _route(router, provider_message_id="dup-1") is None


def test_sender_flood_is_quarantined_after_attribution():
    """Stored and attributed, but not routed — a flood must not bury a clinic's
    real conversations."""
    router = _router(institution=_institution(), over_limit=True)
    result = _route(router)

    assert result.message.status == InboundEmailStatus.QUARANTINED.value
    assert result.message.institution_id == INST_ID


# ---------------------------------------------------------------------------
# Compliance and loops
# ---------------------------------------------------------------------------


def test_opt_out_is_surfaced_for_suppression():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(body_text="Please unsubscribe me"))

    assert result.message.intent == InboundEmailIntent.STOP.value
    assert result.suppress_email_hash is not None
    assert result.needs_staff_attention is False


def test_opt_out_short_circuits_before_threading():
    router = _router(institution=_institution(), contact=_contact())
    _route(router, _parsed(body_text="STOP"))
    router._attach_thread.assert_not_awaited()


def test_auto_reply_is_stored_but_not_escalated():
    """Two autoresponders answering each other generates mail until someone
    notices."""
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(is_auto_reply=True, body_text="I am on leave"))

    assert result.message.intent == InboundEmailIntent.AUTO_REPLY.value
    assert result.needs_staff_attention is False
    router._attach_thread.assert_not_awaited()


def test_bounce_is_stored_but_not_escalated():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(is_bounce=True, body_text="undeliverable"))
    assert result.needs_staff_attention is False


# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------


def test_reply_from_a_different_address_is_flagged():
    """A forwarded copy or a shared family mailbox. The token proves which
    conversation, never who is writing."""
    router = _router(
        institution=_institution(), contact=_contact(email="patient@example.com")
    )
    result = _route(router, _parsed(from_address="spouse@example.com"))

    assert result.message.sender_mismatch is True


def test_reply_from_the_expected_address_is_not_flagged():
    router = _router(
        institution=_institution(), contact=_contact(email="patient@example.com")
    )
    result = _route(router, _parsed(from_address="Patient@Example.com"))
    assert result.message.sender_mismatch is False


def test_addresses_are_masked_not_stored_in_clear():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router)

    assert result.message.from_email_masked == "p***@example.com"
    assert result.message.from_email_hash
    assert "patient@example.com" not in (result.message.from_email_masked or "")


# ---------------------------------------------------------------------------
# Escalation
# ---------------------------------------------------------------------------


def test_bare_confirmation_does_not_need_a_human():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(body_text="yes"))

    assert result.message.intent == InboundEmailIntent.CONFIRM.value
    assert result.needs_staff_attention is False


def test_clinical_reply_needs_a_human():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(
        router, _parsed(body_text="My tooth is still hurting and I have a fever")
    )

    assert result.message.intent == InboundEmailIntent.FREE_TEXT.value
    assert result.needs_staff_attention is True


def test_question_needs_a_human():
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(body_text="What time should I arrive?"))
    assert result.needs_staff_attention is True


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def test_oversized_body_is_truncated_with_a_reason():
    router = _router(institution=_institution(), contact=_contact())
    with patch("src.app.services.email.inbound_router.settings") as s:
        s.inbound_email_max_body_bytes = 20
        s.inbound_email_sender_hourly_limit = 60
        result = asyncio.run(router.route(_parsed(body_text="x" * 500)))

    assert len(result.message.body) <= 20
    assert "truncated" in result.message.status_reason.lower()


def test_subject_is_encrypted_like_the_body():
    """Subject lines carry PHI as readily as bodies."""
    router = _router(institution=_institution(), contact=_contact())
    result = _route(router, _parsed(subject="Re: your root canal"))

    assert result.message.subject == "Re: your root canal"
    assert result.message.subject_encrypted != "Re: your root canal"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "address,expected",
    [
        ("jane.doe@example.com", "j***@example.com"),
        ("a@b.io", "a***@b.io"),
        (None, None),
        ("notanemail", None),
    ],
)
def test_mask_email(address, expected):
    assert _mask_email(address) == expected


def test_addresses_differ_is_case_insensitive():
    assert _addresses_differ("a@b.com", "A@B.COM") is False
    assert _addresses_differ("a@b.com", "c@d.com") is True
    assert _addresses_differ(None, "a@b.com") is False


# ---------------------------------------------------------------------------
# Reply-driven workflow resume — schema
# ---------------------------------------------------------------------------


def test_email_reply_wait_is_recognised():
    from src.app.services.automation.definition_schema import (
        EmailReplyWaitConfig,
        WaitNode,
        email_reply_wait_spec,
        sms_reply_wait_spec,
    )

    node = WaitNode(
        id="w1",
        wait_for=EmailReplyWaitConfig(response_window_seconds=3600),
        next_node_id="n2",
    )
    spec = email_reply_wait_spec(node)

    assert spec is not None
    assert spec.node_id == "w1"
    assert spec.response_window_seconds == 3600
    # The two wait kinds must not be confused for one another.
    assert sms_reply_wait_spec(node) is None


def test_email_reply_wait_defaults_to_a_week():
    """People answer email on a slower rhythm than SMS; a 72-hour window would
    treat an ordinary weekend as a non-response."""
    from src.app.services.automation.definition_schema import EmailReplyWaitConfig

    assert EmailReplyWaitConfig().response_window_seconds == 604800


def test_sms_reply_wait_is_not_an_email_wait():
    from src.app.services.automation.definition_schema import (
        SmsReplyWaitConfig,
        WaitNode,
        email_reply_wait_spec,
    )

    node = WaitNode(id="w1", wait_for=SmsReplyWaitConfig(), next_node_id="n2")
    assert email_reply_wait_spec(node) is None


def test_time_wait_is_not_an_email_wait():
    from src.app.services.automation.definition_schema import (
        DurationDelay,
        TimeWaitConfig,
        WaitNode,
        email_reply_wait_spec,
    )

    node = WaitNode(
        id="w1",
        wait_for=TimeWaitConfig(delay=DurationDelay(duration_seconds=60)),
        next_node_id="n2",
    )
    assert email_reply_wait_spec(node) is None


def test_email_reply_trigger_normalises_tokens():
    from src.app.services.automation.definition_schema import EmailReplyTrigger

    trigger = EmailReplyTrigger(tokens=[" Yes ", "yes", "NO", ""])
    assert trigger.tokens == ["Yes", "NO"]


def test_legacy_sms_reply_key_is_still_dropped():
    """The deprecated include_reply_key must keep loading on published
    definitions — adding the email variant must not disturb that."""
    from src.app.services.automation.definition_schema import SmsReplyWaitConfig

    config = SmsReplyWaitConfig.model_validate(
        {"type": "sms_reply", "include_reply_key": True, "response_window_seconds": 3600}
    )
    assert config.response_window_seconds == 3600
