"""Unit tests for the SendEmailNode recipient resolver, retries and on_failure.

Covers the Phase 1 behaviour added on top of Plan 05's patient-only executor:
staff/static/merge-field recipients, the consent-gate and unsubscribe-footer
branching that depends on them, ``max_attempts`` and ``on_failure``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.definition_schema import (
    ContactRecipient,
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


def _run_with_capture(executor, run, node, post_impl=None, settings_patch=None):
    """Execute the node with Resend stubbed out, returning the captured payload."""
    captured: dict = {}

    async def _default_post(url, headers, json):
        captured["payload"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        resp.json = MagicMock(return_value={"id": "resend-1"})
        return resp

    post = post_impl or _default_post

    with patch("src.app.services.automation.email_node_executor.settings") as s:
        s.resend_api_key = "key"
        s.resend_from_email = "platform@scalenexus.ai"
        s.resend_reply_to = None
        s.public_base_url = "https://api.example.com"
        if settings_patch:
            settings_patch(s)

        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.post = AsyncMock(side_effect=post)

        with patch(
            "src.app.services.automation.email_node_executor.httpx.AsyncClient",
            return_value=client,
        ):
            result = asyncio.run(executor.execute(run, node, {}))

    captured["result"] = result
    captured["post_calls"] = client.post.await_count
    return captured


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


def test_max_attempts_retries_then_succeeds():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    calls = {"n": 0}

    async def _flaky(url, headers, json):
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] < 3:
            resp.status_code = 500
            resp.text = "boom"
            return resp
        resp.status_code = 200
        resp.json = MagicMock(return_value={"id": "resend-1"})
        return resp

    with patch(
        "src.app.services.automation.email_node_executor.asyncio.sleep",
        new=AsyncMock(),
    ):
        out = _run_with_capture(
            executor, _make_run(), _node(max_attempts=3), post_impl=_flaky
        )

    assert out["post_calls"] == 3
    runtime.fail_run.assert_not_called()
    runtime.complete_step.assert_called_once()


def test_max_attempts_exhausted_fails_run_by_default():
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )

    async def _always_fail(url, headers, json):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "boom"
        return resp

    with patch(
        "src.app.services.automation.email_node_executor.asyncio.sleep",
        new=AsyncMock(),
    ):
        out = _run_with_capture(
            executor, _make_run(), _node(max_attempts=2), post_impl=_always_fail
        )

    assert out["post_calls"] == 2
    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()


def test_on_failure_continue_advances_without_failing_the_run():
    """An optional courtesy email should not abandon a workflow that has
    already done its real work."""
    executor, runtime = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )

    async def _always_fail(url, headers, json):
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "boom"
        return resp

    out = _run_with_capture(
        executor,
        _make_run(),
        _node(on_failure="continue"),
        post_impl=_always_fail,
    )

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_not_called()
    assert out["result"] == "n2"


def test_idempotency_key_is_stable_across_attempts():
    """A retry must reuse the key so Resend dedupes rather than double-sending."""
    executor, _ = _make_executor(
        contact=_make_contact(), institution=_make_institution()
    )
    out = _run_with_capture(executor, _make_run(), _node())
    assert out["headers"]["Idempotency-Key"] == "email:run-1:n1"
