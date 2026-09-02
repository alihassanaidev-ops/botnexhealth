"""Unit tests for the SendEmailNode recipient resolver, retries and on_failure.

Covers the Phase 1 behaviour added on top of Plan 05's patient-only executor:
staff/static/merge-field recipients, the consent-gate and unsubscribe-footer
branching that depends on them, ``max_attempts`` and ``on_failure``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import FakeEmailSender, make_resolved_identity
from src.app.services.automation.definition_schema import (
    MergeFieldRecipient,
    SendEmailNode,
    StaffRecipient,
    StaticRecipient,
)
from src.app.services.automation.step_dispatcher import _is_patient_directed


# ---------------------------------------------------------------------------
# Schema — backward compatibility and validation
# ---------------------------------------------------------------------------


def test_node_without_recipient_defaults_to_contact():
    """Definitions published before ``recipient`` existed must still load and
    must keep their original patient-directed behaviour."""
    node = SendEmailNode(
        id="n1",
        subject_template="s",
        body_template="b",
        next_node_id="n2",
    )
    assert node.recipient.kind == "contact"
    assert node.is_patient_directed is True
    assert node.on_failure == "fail_run"


def test_staff_recipient_is_not_patient_directed():
    node = SendEmailNode(
        id="n1",
        subject_template="s",
        body_template="b",
        next_node_id="n2",
        recipient=StaffRecipient(notification_type="urgent_alert"),
    )
    assert node.is_patient_directed is False


def test_static_recipient_is_not_patient_directed():
    node = SendEmailNode(
        id="n1",
        subject_template="s",
        body_template="b",
        next_node_id="n2",
        recipient=StaticRecipient(addresses=["ops@clinic.com"]),
    )
    assert node.is_patient_directed is False


def test_merge_field_recipient_is_treated_as_patient_directed():
    """Conservative default: a merge field usually resolves to the contact, and
    keeping the consent check is the recoverable failure mode."""
    node = SendEmailNode(
        id="n1",
        subject_template="s",
        body_template="b",
        next_node_id="n2",
        recipient=MergeFieldRecipient(field="patient_email"),
    )
    assert node.is_patient_directed is True


def test_static_recipient_rejects_invalid_address():
    with pytest.raises(ValueError):
        StaticRecipient(addresses=["not-an-email"])


def test_static_recipient_requires_at_least_one_address():
    with pytest.raises(ValueError):
        StaticRecipient(addresses=[])


def test_unknown_recipient_kind_is_rejected():
    with pytest.raises(ValueError):
        SendEmailNode(
            id="n1",
            subject_template="s",
            body_template="b",
            next_node_id="n2",
            recipient={"kind": "nope"},
        )


# ---------------------------------------------------------------------------
# Dispatcher gate branching
# ---------------------------------------------------------------------------


def _node(**kw):
    base = dict(
        id="n1", subject_template="s", body_template="b {{clinic_name}}", next_node_id="n2"
    )
    base.update(kw)
    return SendEmailNode(**base)


def test_dispatcher_gates_patient_email():
    assert _is_patient_directed(_node()) is True


def test_dispatcher_skips_gate_for_staff_email():
    """A patient's marketing opt-out must not silently drop an internal alert."""
    assert _is_patient_directed(_node(recipient=StaffRecipient())) is False


def test_dispatcher_gates_non_email_nodes():
    """SMS and voice are always patient contact."""
    assert _is_patient_directed(MagicMock()) is True


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _make_run(contact_id="c-1", location_id="l-1", institution_id="inst-1", context=None):
    run = MagicMock()
    run.id = "run-1"
    run.workflow_id = "wf-1"
    run.institution_id = institution_id
    run.contact_id = contact_id
    run.location_id = location_id
    run.context = context or {}
    return run


def _make_contact(email="patient@example.com"):
    c = MagicMock()
    c.email = email
    c.first_name = "Jane"
    c.last_name = "Doe"
    c.full_name = None
    c.phone = "+14165551234"
    return c


def _make_institution(from_address="noreply@clinic.com", from_name="Clinic"):
    inst = MagicMock()
    inst.email_from_address = from_address
    inst.email_from_name = from_name
    return inst


def _make_executor(contact=None, institution=None, location=None):
    from src.app.services.automation.email_node_executor import EmailNodeExecutor

    session = AsyncMock()
    session.execute.return_value.scalar_one_or_none = MagicMock(return_value=None)
    runtime = AsyncMock()

    async def _get(model, pk):
        from src.app.models.contact import Contact
        from src.app.models.institution import Institution
        from src.app.models.institution_location import InstitutionLocation

        if model is Contact:
            return contact
        if model is Institution:
            return institution
        if model is InstitutionLocation:
            return location
        return None

    session.get = AsyncMock(side_effect=_get)
    runtime.already_sent = AsyncMock(return_value=False)
    runtime.begin_step = AsyncMock(return_value=MagicMock())
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()

    return EmailNodeExecutor(session, runtime), runtime


def _run_with_capture(executor, run, node, sender=None, identity=None):
    """Execute the node with the provider and identity stubbed out.

    Returns the message the sender received, so assertions are about what was
    sent rather than about one vendor's HTTP payload shape.
    """
    sender = sender or FakeEmailSender()
    resolved = identity or make_resolved_identity()

    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=resolved)

    with patch("src.app.services.automation.email_node_executor.settings") as s:
        s.resend_reply_to = None
        s.public_base_url = "https://api.example.com"

        with patch(
            "src.app.services.automation.email_node_executor.EmailIdentityService",
            return_value=resolver,
        ), patch(
            "src.app.services.automation.email_node_executor.get_patient_email_sender_for",
            return_value=sender,
        ):
            result = asyncio.run(executor.execute(run, node, {}))

    message = sender.last
    return {
        "result": result,
        "message": message,
        "payload": _as_payload(message),
        "post_calls": sender.attempts,
        "sender": sender,
    }


def _as_payload(message):
    """Shape the message like the old provider payload so assertions read the same."""
    if message is None:
        return {}
    payload = {
        "to": list(message.to),
        "subject": message.subject,
        "text": message.text,
    }
    if message.html:
        payload["html"] = message.html
    return payload


def test_contact_recipient_sends_to_patient_with_unsubscribe_footer():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    out = _run_with_capture(executor, _make_run(), _node())

    assert out["payload"]["to"] == ["patient@example.com"]
    assert "unsubscribe" in out["payload"]["text"].lower()
    runtime.fail_run.assert_not_called()


def test_staff_recipient_resolves_staff_and_omits_unsubscribe_footer():
    """The footer on a staff alert would write a *patient* consent revocation
    keyed on the staff member's address if clicked."""
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    node = _node(recipient=StaffRecipient(notification_type="urgent_alert"))

    with patch(
        "src.app.services.automation.email_node_executor.resolve_staff_recipients",
        new=AsyncMock(return_value=["admin@clinic.com", "front@clinic.com"]),
    ):
        out = _run_with_capture(executor, _make_run(), node)

    assert out["payload"]["to"] == ["admin@clinic.com", "front@clinic.com"]
    assert "unsubscribe" not in out["payload"]["text"].lower()
    runtime.fail_run.assert_not_called()


def test_staff_recipient_fails_when_no_staff_resolved():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    node = _node(recipient=StaffRecipient())

    with patch(
        "src.app.services.automation.email_node_executor.resolve_staff_recipients",
        new=AsyncMock(return_value=[]),
    ):
        _run_with_capture(executor, _make_run(), node)

    runtime.fail_run.assert_called_once()
    assert "no active staff recipients" in runtime.fail_run.call_args.kwargs["reason"]


def test_static_recipient_sends_to_fixed_addresses():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    node = _node(recipient=StaticRecipient(addresses=["ops@clinic.com"]))
    out = _run_with_capture(executor, _make_run(), node)

    assert out["payload"]["to"] == ["ops@clinic.com"]
    assert "unsubscribe" not in out["payload"]["text"].lower()


def test_staff_email_sends_without_a_contact_on_the_run():
    """An internal alert must not require a patient to be enrolled."""
    executor, runtime = _make_executor(contact=None, institution=_make_institution())
    node = _node(recipient=StaticRecipient(addresses=["ops@clinic.com"]))
    out = _run_with_capture(executor, _make_run(contact_id=None), node)

    assert out["payload"]["to"] == ["ops@clinic.com"]
    runtime.fail_run.assert_not_called()


def test_contact_recipient_still_requires_a_contact():
    executor, runtime = _make_executor(contact=None, institution=_make_institution())
    _run_with_capture(executor, _make_run(contact_id=None), _node())

    runtime.fail_run.assert_called_once()
    assert "no contact_id" in runtime.fail_run.call_args.kwargs["reason"]


# ---------------------------------------------------------------------------
# Retries and on_failure
# ---------------------------------------------------------------------------


def _retryable(message="boom"):
    from src.app.services.email.sender import EmailSendError

    return EmailSendError(message, retryable=True)


def _permanent(message="rejected"):
    from src.app.services.email.sender import EmailSendError

    return EmailSendError(message, retryable=False)


def test_max_attempts_retries_then_succeeds():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    sender = FakeEmailSender(fail_with=_retryable(), fail_times=2)

    with patch(
        "src.app.services.automation.email_node_executor.asyncio.sleep",
        new=AsyncMock(),
    ):
        out = _run_with_capture(
            executor, _make_run(), _node(max_attempts=3), sender=sender
        )

    assert out["post_calls"] == 3
    runtime.fail_run.assert_not_called()
    runtime.complete_step.assert_called_once()


def test_max_attempts_exhausted_fails_run_by_default():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    sender = FakeEmailSender(fail_with=_retryable(), fail_times=99)

    with patch(
        "src.app.services.automation.email_node_executor.asyncio.sleep",
        new=AsyncMock(),
    ):
        out = _run_with_capture(
            executor, _make_run(), _node(max_attempts=2), sender=sender
        )

    assert out["post_calls"] == 2
    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()


def test_permanent_failure_is_not_retried():
    """A rejected address is rejected identically next time; retrying only
    burns sending quota."""
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    sender = FakeEmailSender(fail_with=_permanent(), fail_times=99)

    out = _run_with_capture(
        executor, _make_run(), _node(max_attempts=3), sender=sender
    )

    assert out["post_calls"] == 1
    runtime.fail_run.assert_called_once()


def test_on_failure_continue_advances_without_failing_the_run():
    """An optional courtesy email should not abandon a workflow that has
    already done its real work."""
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    sender = FakeEmailSender(fail_with=_retryable(), fail_times=99)

    out = _run_with_capture(
        executor, _make_run(), _node(on_failure="continue"), sender=sender
    )

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_not_called()
    assert out["result"] == "n2"


def test_idempotency_key_is_stable_across_attempts():
    """A retry must reuse the key so the provider dedupes rather than
    double-sending."""
    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    out = _run_with_capture(executor, _make_run(), _node())
    assert out["message"].idempotency_key == "email:run-1:n1"


def test_identity_supplies_the_from_address_and_provider():
    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    sender = FakeEmailSender(provider="ses")
    identity = make_resolved_identity(
        from_address="hello@brightsmile.mail.scalenexus.ai",
        from_name="Bright Smile Dental",
        provider="ses",
        tenant_name="brightsmile",
        configuration_set="scalenexus-brightsmile",
    )

    out = _run_with_capture(
        executor, _make_run(), _node(), sender=sender, identity=identity
    )

    msg = out["message"]
    assert msg.from_address == "hello@brightsmile.mail.scalenexus.ai"
    assert msg.from_name == "Bright Smile Dental"
    assert msg.tenant_name == "brightsmile"
    assert msg.configuration_set == "scalenexus-brightsmile"


def test_no_sending_address_fails_the_step():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    identity = make_resolved_identity(from_address=None, is_platform_fallback=True)

    _run_with_capture(executor, _make_run(), _node(), identity=identity)

    runtime.fail_run.assert_called_once()
    assert "no sending address" in runtime.fail_run.call_args.kwargs["reason"]


# ---------------------------------------------------------------------------
# Reply-To — routing patient replies back to the conversation
# ---------------------------------------------------------------------------


def _run_with_inbound(executor, node, inbound_domain="inbound.example.com", **kw):
    """Execute with an inbound receiving domain configured."""
    sender = kw.pop("sender", None) or FakeEmailSender()
    resolved = kw.pop("identity", None) or make_resolved_identity(
        reply_to="frontdesk@clinic.com"
    )
    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=resolved)

    with patch("src.app.services.automation.email_node_executor.settings") as s:
        s.resend_reply_to = None
        s.public_base_url = "https://api.example.com"
        s.ses_inbound_domain = inbound_domain

        with patch(
            "src.app.services.automation.email_node_executor.EmailIdentityService",
            return_value=resolver,
        ), patch(
            "src.app.services.automation.email_node_executor.get_patient_email_sender_for",
            return_value=sender,
        ):
            asyncio.run(executor.execute(_make_run(), node, {}))
    return sender.last


def test_patient_email_replies_to_a_signed_routing_address():
    from src.app.services.email.reply_address import parse_reply_address

    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    message = _run_with_inbound(executor, _node())

    assert message.reply_to.endswith("@inbound.example.com")
    route = parse_reply_address(message.reply_to)
    assert route is not None
    # The token carries the run so a reply can resume the workflow that sent it.
    assert route.run_prefix


def test_staff_email_keeps_the_clinic_reply_to():
    """Pointing staff mail at the inbound router would file colleagues' replies
    as patient messages."""
    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    node = _node(recipient=StaticRecipient(addresses=["ops@clinic.com"]))

    message = _run_with_inbound(executor, node)

    assert message.reply_to == "frontdesk@clinic.com"


def test_no_inbound_domain_keeps_the_clinic_reply_to():
    """Before inbound is stood up, replies should still reach the clinic."""
    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    message = _run_with_inbound(executor, _node(), inbound_domain=None)

    assert message.reply_to == "frontdesk@clinic.com"
