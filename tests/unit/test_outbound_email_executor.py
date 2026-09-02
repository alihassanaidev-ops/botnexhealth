"""Unit tests for Plan 05 — Outbound Email (EmailNodeExecutor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import FakeEmailSender, make_resolved_identity
from src.app.services.automation.definition_schema import SendEmailNode
from src.app.services.email.sender import EmailMessage, _formatted_from


# ---------------------------------------------------------------------------
# From-header formatting
#
# Moved to services.email.sender when the provider seam was introduced, so both
# Resend and SES format the header the same way.
# ---------------------------------------------------------------------------


def _msg(from_address: str, from_name: str | None) -> EmailMessage:
    return EmailMessage(
        from_address=from_address,
        from_name=from_name,
        to=["patient@example.com"],
        subject="s",
        text="t",
    )


def test_build_from_with_name():
    assert (
        _formatted_from(_msg("noreply@clinic.com", "Sunny Dental"))
        == "Sunny Dental <noreply@clinic.com>"
    )


def test_build_from_without_name():
    assert _formatted_from(_msg("noreply@clinic.com", None)) == "noreply@clinic.com"


def test_build_from_empty_name():
    assert _formatted_from(_msg("noreply@clinic.com", "")) == "noreply@clinic.com"


# ---------------------------------------------------------------------------
# EmailNodeExecutor
# ---------------------------------------------------------------------------


def _make_run(contact_id="c-1", location_id="l-1", institution_id="inst-1"):
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = institution_id
    run.contact_id = contact_id
    run.location_id = location_id
    return run


def _make_node(subject="Hi {{patient_first_name}}", body="Reminder from {{clinic_name}}", next_id="node-2"):
    return SendEmailNode(
        id="node-1",
        subject_template=subject,
        body_template=body,
        next_node_id=next_id,
    )


def _make_contact(email="patient@example.com", first="Jane"):
    c = MagicMock()
    c.email = email
    c.first_name = first
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
    runtime.already_sent = AsyncMock(return_value=False)  # no prior send by default
    runtime.begin_step = AsyncMock(return_value=MagicMock())
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()

    return EmailNodeExecutor(session, runtime), runtime


def _fail_reason(runtime) -> str:
    return runtime.fail_run.call_args.kwargs.get("reason", "")


def test_executor_fails_when_no_contact_id():
    executor, runtime = _make_executor()
    run = _make_run(contact_id=None)
    asyncio.run(executor.execute(run, _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no contact_id" in _fail_reason(runtime)


def test_executor_fails_when_contact_not_found():
    executor, runtime = _make_executor(contact=None)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "not found" in _fail_reason(runtime)


def test_executor_fails_when_no_email():
    contact = _make_contact(email=None)
    executor, runtime = _make_executor(contact=contact)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no email" in _fail_reason(runtime)


# ---------------------------------------------------------------------------
# Sending — provider-agnostic
#
# Which vendor carries the message is resolved per clinic now, so these assert
# against the message handed to the sender rather than one vendor's HTTP shape.
# The from-address resolution itself moved to EmailIdentityService and is
# covered in test_email_sending_identity.py.
# ---------------------------------------------------------------------------


def _execute(executor, node=None, run=None, sender=None, identity=None):
    sender = sender or FakeEmailSender()
    resolver = AsyncMock()
    resolver.resolve = AsyncMock(return_value=identity or make_resolved_identity())

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
            result = asyncio.run(executor.execute(run or _make_run(), node or _make_node(), {}))
    return result, sender


def test_executor_fails_when_no_sending_address_resolves():
    executor, runtime = _make_executor(contact=_make_contact())
    identity = make_resolved_identity(from_address=None, is_platform_fallback=True)

    _execute(executor, identity=identity)

    runtime.fail_run.assert_called_once()
    assert "no sending address" in _fail_reason(runtime)


def test_executor_uses_the_resolved_identity():
    executor, runtime = _make_executor(contact=_make_contact())
    identity = make_resolved_identity(
        from_address="clinic@example.com", from_name="My Clinic"
    )

    result, sender = _execute(executor, identity=identity)

    assert result == "node-2"
    message = sender.last
    assert message.from_address == "clinic@example.com"
    assert message.from_name == "My Clinic"
    assert message.to == ["patient@example.com"]
    # XC-1b: stable per-(run, node) idempotency key handed to the provider.
    assert message.idempotency_key == "email:run-1:node-1"
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "sent"
    runtime.fail_run.assert_not_called()


def test_executor_tags_the_message_with_the_institution():
    """Bounce and complaint suppression is scoped back to the owning clinic."""
    executor, _ = _make_executor(contact=_make_contact())
    _, sender = _execute(executor)
    assert sender.last.institution_id == "inst-1"


def test_executor_is_idempotent_when_already_sent():
    """A redelivery / hold-resume that re-enters an already-sent node must NOT
    email the patient again — it advances silently."""
    executor, runtime = _make_executor(contact=_make_contact())
    runtime.already_sent = AsyncMock(return_value=True)

    result, sender = _execute(executor)

    assert result == "node-2"           # still advances
    assert sender.attempts == 0         # but never re-sends
    runtime.begin_step.assert_not_called()
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


def test_executor_fails_on_provider_error():
    from src.app.services.email.sender import EmailSendError

    executor, runtime = _make_executor(contact=_make_contact())
    sender = FakeEmailSender(
        fail_with=EmailSendError("Unprocessable", retryable=False), fail_times=99
    )

    _execute(executor, sender=sender)

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_email error" in _fail_reason(runtime)


def test_executor_does_not_retry_an_unknown_transport_outcome():
    """A timeout can happen after acceptance; retrying it may duplicate mail."""
    executor, runtime = _make_executor(contact=_make_contact())
    sender = FakeEmailSender(fail_with=TimeoutError("socket closed"), fail_times=99)

    _execute(executor, node=_make_node(), sender=sender)

    assert sender.attempts == 1
    runtime.fail_run.assert_called_once()


def test_ses_transport_error_is_explicitly_uncertain():
    from botocore.exceptions import EndpointConnectionError

    from src.app.services.email.sender import EmailSendError, SesSender

    client = MagicMock()
    client.send_email.side_effect = EndpointConnectionError(endpoint_url="https://ses")

    with pytest.raises(EmailSendError) as exc:
        SesSender(client=client)._send_sync(_msg("clinic@example.com", "Clinic"))

    assert exc.value.retryable is False
    assert exc.value.outcome_uncertain is True
