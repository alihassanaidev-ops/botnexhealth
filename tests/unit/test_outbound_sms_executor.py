"""Unit tests for Plan 04 — Outbound SMS (template renderer + SmsNodeExecutor)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


def test_render_nested_appointment_context():
    result = render_sms_body(
        "Hi {{patient_first_name}}, provider {{provider_id}} sees you {{appointment_date}} at {{appointment_time}}.",
        None,
        None,
        {
            "patient_first_name": "Sam",
            "appointment": {
                "start_time": "2026-07-22T14:00:00+00:00",
                "provider_id": "gt-2",
            },
        },
    )
    assert result == "Hi Sam, provider gt-2 sees you July 22, 2026 at 2:00 PM."


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
    run.workflow_id = "wf-1"
    run.institution_id = "inst-1"
    run.contact_id = contact_id
    run.location_id = location_id
    return run


def _make_node(
    body_template="Hi {{patient_first_name}}",
    next_id="node-2",
    *,
    include_opt_out_footer=True,
):
    return SendSmsNode(
        id="node-1",
        body_template=body_template,
        next_node_id=next_id,
        include_opt_out_footer=include_opt_out_footer,
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
    runtime.already_sent = AsyncMock(return_value=False)  # no prior send by default
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

    thread = MagicMock(id="thread-1")
    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms, patch(
        "src.app.services.automation.sms_node_executor.CampaignConversationService"
    ) as MockThreads:
        MockThreads.return_value.open_sms_thread = AsyncMock(return_value=thread)
        MockThreads.return_value.mark_message_seen = AsyncMock()
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(return_value=MagicMock())
        result = asyncio.run(executor.execute(run, node, {}))

    assert result == "node-2"
    # Campaign attribution (Plan 11): run + workflow ids flow to send_sms so the
    # delivery webhook can tag the usage event for /by-campaign.
    send_kwargs = instance.send_sms.call_args.kwargs
    assert send_kwargs["workflow_run_id"] == "run-1"
    assert send_kwargs["workflow_id"] == "wf-1"
    assert send_kwargs["conversation_thread_id"] == "thread-1"
    assert send_kwargs["include_opt_out_footer"] is True
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "sent"
    runtime.fail_run.assert_not_called()


def test_executor_can_disable_opt_out_footer():
    contact = _make_contact()
    location = _make_location()
    executor, _runtime = _make_executor(contact=contact, location=location)

    thread = MagicMock(id="thread-1")
    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms, patch(
        "src.app.services.automation.sms_node_executor.CampaignConversationService"
    ) as MockThreads:
        MockThreads.return_value.open_sms_thread = AsyncMock(return_value=thread)
        MockThreads.return_value.mark_message_seen = AsyncMock()
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(return_value=MagicMock())
        asyncio.run(executor.execute(_make_run(), _make_node(include_opt_out_footer=False), {}))

    assert instance.send_sms.call_args.kwargs["include_opt_out_footer"] is False


def test_executor_is_idempotent_when_already_sent():
    """A redelivery / hold-resume that re-enters an already-sent node must NOT
    text the patient again — it advances silently."""
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)
    runtime.already_sent = AsyncMock(return_value=True)
    run = _make_run()
    node = _make_node()

    with patch("src.app.services.automation.sms_node_executor.SmsService") as MockSms:
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(return_value=MagicMock())
        result = asyncio.run(executor.execute(run, node, {}))

    assert result == "node-2"                 # still advances
    instance.send_sms.assert_not_called()     # but never re-sends
    runtime.begin_step.assert_not_called()
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


def test_executor_fails_run_on_twilio_error():
    contact = _make_contact()
    location = _make_location()
    executor, runtime = _make_executor(contact=contact, location=location)
    run = _make_run()
    node = _make_node()

    thread = MagicMock(id="thread-1")
    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms, patch(
        "src.app.services.automation.sms_node_executor.CampaignConversationService"
    ) as MockThreads:
        MockThreads.return_value.open_sms_thread = AsyncMock(return_value=thread)
        MockThreads.return_value.mark_message_seen = AsyncMock()
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(side_effect=RuntimeError("Twilio boom"))
        asyncio.run(executor.execute(run, node, {}))

    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_sms error" in _fail_run_reason(runtime)


# ---------------------------------------------------------------------------
# Item 14 — retry, classification, and never texting a patient twice
# ---------------------------------------------------------------------------


def _sent_log():
    from src.app.models.sms_history_log import SmsStatus
    return MagicMock(status=SmsStatus.SENT.value, provider_status="queued")


def _failed_log(provider_status):
    from src.app.models.sms_history_log import SmsStatus
    return MagicMock(status=SmsStatus.FAILED.value, provider_status=provider_status)


def _run_with_sends(node, *logs):
    """Drive the executor with a scripted sequence of send_sms outcomes."""
    executor, runtime = _make_executor(contact=_make_contact(), location=_make_location())
    thread = MagicMock(id="thread-1")
    with patch(
        "src.app.services.automation.sms_node_executor.SmsService"
    ) as MockSms, patch(
        "src.app.services.automation.sms_node_executor.CampaignConversationService"
    ) as MockThreads, patch(
        "src.app.services.automation.sms_node_executor.asyncio.sleep", new=AsyncMock()
    ):
        MockThreads.return_value.open_sms_thread = AsyncMock(return_value=thread)
        MockThreads.return_value.mark_message_seen = AsyncMock()
        instance = MockSms.return_value
        instance.send_sms = AsyncMock(side_effect=list(logs))
        asyncio.run(executor.execute(_make_run(), node, {}))
    return runtime, instance


def _result_code(runtime) -> str:
    return runtime.fail_step.call_args.kwargs.get("result_code", "")


def test_provider_failure_is_no_longer_recorded_as_sent():
    """The regression this item exists to close.

    SmsService swallows provider errors and returns a FAILED row rather than
    raising. The executor ignored that return value, so a message that never
    left Twilio was completed as "sent" and the campaign moved on.
    """
    runtime, _ = _run_with_sends(_make_node(), _failed_log("failed:21610"))
    runtime.complete_step.assert_not_called()
    runtime.fail_step.assert_called_once()
    assert _result_code(runtime) == "send_failed_permanent"


def test_permanent_failure_is_not_retried():
    node = _make_node()
    node.max_attempts = 3
    _runtime, instance = _run_with_sends(node, _failed_log("failed:21610"))
    assert instance.send_sms.await_count == 1, "a rejected number will reject again"


def test_retryable_rejection_is_retried_then_succeeds():
    node = _make_node()
    node.max_attempts = 3
    runtime, instance = _run_with_sends(
        node, _failed_log("retryable:503"), _sent_log()
    )
    assert instance.send_sms.await_count == 2
    runtime.complete_step.assert_called_once()
    assert runtime.complete_step.call_args.kwargs.get("result_code") == "sent"
    runtime.fail_run.assert_not_called()


def test_retryable_rejection_exhausts_attempts():
    node = _make_node()
    node.max_attempts = 3
    runtime, instance = _run_with_sends(
        node, _failed_log("retryable:429"), _failed_log("retryable:429"),
        _failed_log("retryable:429"),
    )
    assert instance.send_sms.await_count == 3
    assert _result_code(runtime) == "send_failed_retries_exhausted"


def test_ambiguous_network_failure_is_never_retried():
    """A lost response may mean the text went out. Retrying sends it twice.

    Same reasoning the voice path already applies to its ambiguous class.
    """
    node = _make_node()
    node.max_attempts = 3
    runtime, instance = _run_with_sends(node, _failed_log("retryable:network"))
    assert instance.send_sms.await_count == 1, "must not risk a second text"
    assert _result_code(runtime) == "send_failed_ambiguous"


def test_default_max_attempts_sends_once():
    """max_attempts defaults to 1, so existing campaigns are unchanged."""
    _runtime, instance = _run_with_sends(_make_node(), _failed_log("retryable:503"))
    assert instance.send_sms.await_count == 1
