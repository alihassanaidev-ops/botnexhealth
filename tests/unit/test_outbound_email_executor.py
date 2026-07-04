"""Unit tests for Plan 05 — Outbound Email (EmailNodeExecutor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.definition_schema import SendEmailNode
from src.app.services.automation.email_node_executor import _build_from


# ---------------------------------------------------------------------------
# _build_from helper
# ---------------------------------------------------------------------------


def test_build_from_with_name():
    assert _build_from("noreply@clinic.com", "Sunny Dental") == "Sunny Dental <noreply@clinic.com>"


def test_build_from_without_name():
    assert _build_from("noreply@clinic.com", None) == "noreply@clinic.com"


def test_build_from_empty_name():
    assert _build_from("noreply@clinic.com", "") == "noreply@clinic.com"


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


def test_executor_fails_when_resend_not_configured():
    contact = _make_contact()
    institution = _make_institution(from_address=None)
    executor, runtime = _make_executor(contact=contact, institution=institution)

    with patch("src.app.services.automation.email_node_executor.settings") as mock_settings:
        mock_settings.resend_api_key = None
        mock_settings.resend_from_email = None
        mock_settings.resend_reply_to = None
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    runtime.fail_run.assert_called_once()
    assert "Resend not configured" in _fail_reason(runtime)


def test_executor_uses_institution_from_address():
    """Institution from_address takes priority over platform settings."""
    contact = _make_contact()
    institution = _make_institution(from_address="clinic@example.com", from_name="My Clinic")
    executor, runtime = _make_executor(contact=contact, institution=institution)

    captured = {}

    async def _fake_post(url, headers, json):
        captured["payload"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    with (
        patch("src.app.services.automation.email_node_executor.settings") as mock_settings,
        patch("src.app.services.automation.email_node_executor.httpx.AsyncClient") as MockClient,
    ):
        mock_settings.resend_api_key = "re_test"
        mock_settings.resend_from_email = "platform@example.com"
        mock_settings.resend_reply_to = None
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=_fake_post)
        MockClient.return_value = mock_http

        result = asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    assert result == "node-2"
    assert captured["payload"]["from"] == "My Clinic <clinic@example.com>"
    assert captured["payload"]["to"] == [contact.email]
    # XC-1b: stable per-(run,node) idempotency key sent to Resend.
    assert captured["headers"]["Idempotency-Key"] == "email:run-1:node-1"
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "sent"
    runtime.fail_run.assert_not_called()


def test_executor_falls_back_to_platform_from_address():
    contact = _make_contact()
    institution = _make_institution(from_address=None, from_name=None)
    executor, runtime = _make_executor(contact=contact, institution=institution)

    captured = {}

    async def _fake_post(url, headers, json):
        captured["payload"] = json
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    with (
        patch("src.app.services.automation.email_node_executor.settings") as mock_settings,
        patch("src.app.services.messaging_credentials.settings") as resolver_settings,
        patch("src.app.services.automation.email_node_executor.httpx.AsyncClient") as MockClient,
    ):
        mock_settings.resend_api_key = "re_test"
        mock_settings.resend_from_email = "platform@example.com"
        mock_settings.resend_reply_to = None
        # The from-address fallback is resolved by TenantTwilioCredentialResolver,
        # which reads settings from its own module.
        resolver_settings.resend_from_email = "platform@example.com"
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=_fake_post)
        MockClient.return_value = mock_http

        asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    assert captured["payload"]["from"] == "platform@example.com"


def test_executor_is_idempotent_when_already_sent():
    """A redelivery / hold-resume that re-enters an already-sent node must NOT
    email the patient again — it advances silently."""
    contact = _make_contact()
    institution = _make_institution()
    executor, runtime = _make_executor(contact=contact, institution=institution)
    runtime.already_sent = AsyncMock(return_value=True)

    with patch("src.app.services.automation.email_node_executor.httpx.AsyncClient") as MockClient:
        result = asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    assert result == "node-2"                 # still advances
    MockClient.assert_not_called()            # but never opens an HTTP client / re-sends
    runtime.begin_step.assert_not_called()
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


def test_executor_fails_on_resend_http_error():
    contact = _make_contact()
    institution = _make_institution()
    executor, runtime = _make_executor(contact=contact, institution=institution)

    with (
        patch("src.app.services.automation.email_node_executor.settings") as mock_settings,
        patch("src.app.services.automation.email_node_executor.httpx.AsyncClient") as MockClient,
    ):
        mock_settings.resend_api_key = "re_test"
        mock_settings.resend_from_email = "platform@example.com"
        mock_settings.resend_reply_to = None
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        resp = MagicMock()
        resp.status_code = 422
        resp.text = "Unprocessable"
        mock_http.post = AsyncMock(return_value=resp)
        MockClient.return_value = mock_http

        asyncio.run(executor.execute(_make_run(), _make_node(), {}))

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_email error" in _fail_reason(runtime)
