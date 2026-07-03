"""Unit tests for Plan 04 — Outbound SMS (template renderer + SmsNodeExecutor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.definition_schema import SendSmsNode
from src.app.services.automation.template_renderer import render_sms_body


# ---------------------------------------------------------------------------
# Template renderer
# ---------------------------------------------------------------------------


def _make_contact(first="Jane", last="Doe", full=None, phone="+14165551234"):
    c = MagicMock()
    c.first_name = first
    c.last_name = last
    c.full_name = full
    c.phone = phone
    return c


def _make_location(name="Sunny Dental", from_number="+16475550001"):
    loc = MagicMock()
    loc.name = name
    loc.twilio_from_number = from_number
    return loc


def test_render_known_contact_vars():
    contact = _make_contact(first="Jane", last="Doe")
    result = render_sms_body("Hi {{patient_first_name}} {{patient_last_name}}!", contact, None, {})
    assert result == "Hi Jane Doe!"


def test_render_full_name_uses_column_when_set():
    contact = _make_contact(full="Jane A. Doe")
    result = render_sms_body("Hello {{patient_full_name}}", contact, None, {})
    assert result == "Hello Jane A. Doe"


def test_render_full_name_constructs_from_parts_when_null():
    contact = _make_contact(first="Jane", last="Doe", full=None)
    result = render_sms_body("Hello {{patient_full_name}}", contact, None, {})
    assert result == "Hello Jane Doe"


def test_render_clinic_name():
    location = _make_location(name="Bright Smile Dental")
    result = render_sms_body("From {{clinic_name}}", None, location, {})
    assert result == "From Bright Smile Dental"


def test_render_context_passthrough():
    result = render_sms_body(
        "Appt on {{appointment_date}}", None, None, {"appointment_date": "July 10"}
    )
    assert result == "Appt on July 10"


def test_render_unknown_var_becomes_blank():
    result = render_sms_body("Hello {{unknown_var}}!", None, None, {})
    assert result == "Hello !"


def test_render_no_contact_no_location_known_patient_var_blank():
    result = render_sms_body("Hi {{patient_first_name}}", None, None, {})
    assert result == "Hi "


# ---------------------------------------------------------------------------
# SmsNodeExecutor
# ---------------------------------------------------------------------------


def _make_run(contact_id="c-1", location_id="l-1"):
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.contact_id = contact_id
    run.location_id = location_id
    return run


def _make_node(body_template="Hi {{patient_first_name}}", next_id="node-2"):
    return SendSmsNode(
        id="node-1",
        body_template=body_template,
        next_node_id=next_id,
    )


def _make_executor(contact=None, location=None):
    from src.app.services.automation.sms_node_executor import SmsNodeExecutor

    session = AsyncMock()
    runtime = AsyncMock()

    async def _get(model, pk):
        from src.app.models.contact import Contact
        from src.app.models.institution_location import InstitutionLocation
        if model is Contact:
            return contact
        if model is InstitutionLocation:
            return location
        return None

    session.get = AsyncMock(side_effect=_get)
    runtime.begin_step = AsyncMock(return_value=MagicMock())
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()

    return SmsNodeExecutor(session, runtime), runtime


def _fail_run_reason(runtime) -> str:
    """Extract the `reason` kwarg from the most recent fail_run call."""
    return runtime.fail_run.call_args.kwargs.get("reason", "")


def test_executor_fails_when_no_contact_id():
    executor, runtime = _make_executor()
    run = _make_run(contact_id=None)
    node = _make_node()
    asyncio.run(executor.execute(run, node, {}))
    runtime.fail_run.assert_called_once()
    assert "no contact_id" in _fail_run_reason(runtime)


def test_executor_fails_when_contact_not_found():
    executor, runtime = _make_executor(contact=None)
    run = _make_run()
    node = _make_node()
    asyncio.run(executor.execute(run, node, {}))
    runtime.fail_run.assert_called_once()
    assert "not found" in _fail_run_reason(runtime)


def test_executor_fails_when_no_phone():
    contact = _make_contact(phone=None)
    executor, runtime = _make_executor(contact=contact)
    run = _make_run()
    node = _make_node()
    asyncio.run(executor.execute(run, node, {}))
    runtime.fail_run.assert_called_once()
    assert "no phone" in _fail_run_reason(runtime)


def test_executor_fails_when_no_from_number():
    contact = _make_contact()
    location = _make_location(from_number=None)
    executor, runtime = _make_executor(contact=contact, location=location)
    run = _make_run()
    node = _make_node()
    asyncio.run(executor.execute(run, node, {}))
    runtime.fail_run.assert_called_once()
    assert "twilio_from_number" in _fail_run_reason(runtime)


def test_executor_sends_and_completes_step():
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)
    run = _make_run()
    node = _make_node()

    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms:
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(return_value=MagicMock())
        result = asyncio.run(executor.execute(run, node, {}))

    assert result == "node-2"
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "sent"
    runtime.fail_run.assert_not_called()


def test_executor_fails_run_on_twilio_error():
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)
    run = _make_run()
    node = _make_node()

    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms:
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(side_effect=RuntimeError("Twilio boom"))
        asyncio.run(executor.execute(run, node, {}))

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_sms error" in _fail_run_reason(runtime)
