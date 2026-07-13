"""Unit tests for Plan 03 — Outbound Voice (VoiceNodeExecutor)."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.definition_schema import SendVoiceNode
from src.app.services.automation.retell_outbound_client import (
    RetellAmbiguousError,
    RetellCallResult,
    RetellPermanentError,
    RetellTransientError,
)
from src.app.services.automation.voice_node_executor import VoiceParked


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_run(contact_id="c-1", location_id="l-1", institution_id="inst-1"):
    run = MagicMock()
    run.id = "run-1"
    run.workflow_id = "wf-1"
    run.institution_id = institution_id
    run.contact_id = contact_id
    run.location_id = location_id
    return run


def _make_node(agent_id="agent_abc", next_id="node-2", max_attempts=1, wait_for_outcome=False):
    return SendVoiceNode(
        id="node-1",
        retell_agent_id=agent_id,
        next_node_id=next_id,
        max_attempts=max_attempts,
        wait_for_outcome=wait_for_outcome,
    )


def _make_contact(phone="+14165551234", first="Jane"):
    c = MagicMock()
    c.phone = phone
    c.first_name = first
    c.last_name = "Doe"
    return c


def _make_location(retell_from_number="+15005550000", name="Bright Smiles Dental"):
    loc = MagicMock()
    loc.retell_from_number = retell_from_number
    loc.name = name
    return loc


def _make_executor(
    contact=None, location=None, already_placed=False, attempt_number=1, profile=None,
    claim_id=None,
):
    from src.app.services.automation.voice_node_executor import VoiceNodeExecutor

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
    # Query results: profile lookup reads scalar_one_or_none() (None = fall back to
    # node/location defaults); the P9 claim check reads scalar() (None = not claimed).
    # Distinct accessors on the shared result mock keep the two queries independent.
    exec_result = MagicMock()
    exec_result.scalar_one_or_none = MagicMock(return_value=profile)
    exec_result.scalar = MagicMock(return_value=claim_id)
    session.execute = AsyncMock(return_value=exec_result)
    session.add = MagicMock()  # sync in SQLAlchemy — keep it non-awaitable
    session.commit = AsyncMock()
    runtime.already_sent = AsyncMock(return_value=already_placed)
    step = MagicMock()
    step.id = "step-1"
    step.attempt_number = attempt_number
    runtime.begin_step = AsyncMock(return_value=step)
    runtime.fail_step = AsyncMock()
    runtime.fail_run = AsyncMock()
    runtime.complete_step = AsyncMock()
    runtime.mark_step_awaiting_outcome = AsyncMock()

    return VoiceNodeExecutor(session, runtime), runtime, step


@contextmanager
def _patch_client(*, result=None, side_effect=None, api_key="re_secret"):
    """Patch settings + the mockable RetellOutboundClient. Yields the create_phone_call mock."""
    call_mock = AsyncMock(return_value=result, side_effect=side_effect)
    client_instance = MagicMock()
    client_instance.create_phone_call = call_mock
    with (
        patch("src.app.services.automation.voice_node_executor.settings") as mock_settings,
        patch(
            "src.app.services.automation.voice_node_executor.RetellOutboundClient",
            MagicMock(return_value=client_instance),
        ),
    ):
        mock_settings.retell_api_secret = api_key
        yield call_mock


def _fail_reason(runtime) -> str:
    return runtime.fail_run.call_args.kwargs.get("reason", "")


# ---------------------------------------------------------------------------
# Precondition failure paths (return before the vendor call)
# ---------------------------------------------------------------------------


def test_executor_fails_when_no_contact_id():
    executor, runtime, _ = _make_executor()
    asyncio.run(executor.execute(_make_run(contact_id=None), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no contact_id" in _fail_reason(runtime)


def test_executor_fails_when_contact_not_found():
    executor, runtime, _ = _make_executor(contact=None)
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "not found" in _fail_reason(runtime)


def test_executor_fails_when_no_phone():
    executor, runtime, _ = _make_executor(contact=_make_contact(phone=None))
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "no phone" in _fail_reason(runtime)


def test_executor_fails_when_no_retell_from_number():
    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(retell_from_number=None)
    )
    asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "retell_from_number" in _fail_reason(runtime)


def test_executor_fails_when_retell_not_configured():
    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="x"), api_key=None):
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    runtime.fail_run.assert_called_once()
    assert "Retell not configured" in _fail_reason(runtime)


# ---------------------------------------------------------------------------
# Success (fire-and-forget)
# ---------------------------------------------------------------------------


def test_executor_places_call_and_stores_call_id():
    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="call_xyz")) as call_mock:
        result = asyncio.run(executor.execute(_make_run(), _make_node(agent_id="agent_xyz"), {}))

    assert result == "node-2"
    kw = call_mock.call_args.kwargs
    assert kw["from_number"] == "+15005550000"
    assert kw["to_number"] == "+14165551234"
    assert kw["override_agent_id"] == "agent_xyz"
    dv = kw["dynamic_variables"]
    assert dv["first_name"] == "Jane"
    assert dv["user_number"] == "+14165551234"
    assert dv["clinic_name"] == "Bright Smiles Dental"
    assert "automated call" in dv["compliance_disclosure"].lower()
    assert "stop" in dv["compliance_disclosure"].lower()
    md = kw["metadata"]
    assert md["workflow_run_id"] == "run-1"
    assert md["workflow_id"] == "wf-1"  # attribution for /by-campaign (Plan 11)
    assert md["source"] == "outbound_campaign"
    assert md["ai_automated_call"] is True
    # call_id captured onto the attempt for webhook correlation.
    runtime.complete_step.assert_called_once()
    ckw = runtime.complete_step.call_args.kwargs
    assert ckw.get("result_code") == "call_placed"
    assert ckw.get("result_metadata") == {"retell_call_id": "call_xyz"}
    runtime.fail_run.assert_not_called()


def _make_profile(agent_id="agent_profile", from_number="+15559990000"):
    p = MagicMock()
    p.retell_agent_id = agent_id
    p.retell_from_number = from_number
    return p


def test_executor_profile_overrides_agent_and_from_number():
    """An active outbound-voice profile (V-4) overrides the node agent + location number."""
    executor, runtime, _ = _make_executor(
        contact=_make_contact(),
        location=_make_location(),
        profile=_make_profile(agent_id="agent_profile", from_number="+15559990000"),
    )
    with _patch_client(result=RetellCallResult(call_id="c1")) as call_mock:
        asyncio.run(executor.execute(_make_run(), _make_node(agent_id="agent_node"), {}))
    kw = call_mock.call_args.kwargs
    assert kw["from_number"] == "+15559990000"      # profile wins over location
    assert kw["override_agent_id"] == "agent_profile"  # profile wins over node


def test_executor_falls_back_when_profile_fields_blank():
    """A profile with empty agent/number falls back to node/location defaults."""
    executor, runtime, _ = _make_executor(
        contact=_make_contact(),
        location=_make_location(retell_from_number="+15005550000"),
        profile=_make_profile(agent_id=None, from_number=None),
    )
    with _patch_client(result=RetellCallResult(call_id="c1")) as call_mock:
        asyncio.run(executor.execute(_make_run(), _make_node(agent_id="agent_node"), {}))
    kw = call_mock.call_args.kwargs
    assert kw["from_number"] == "+15005550000"
    assert kw["override_agent_id"] == "agent_node"


def test_executor_records_voice_attempt_on_success():
    """A placed fire-and-forget call inserts a WorkflowVoiceAttempt row (status=placed)."""
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="call_rec")):
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    added = [c.args[0] for c in executor.session.add.call_args_list]
    attempts = [o for o in added if isinstance(o, WorkflowVoiceAttempt)]
    assert len(attempts) == 1
    attempt = attempts[0]
    assert attempt.retell_call_id == "call_rec"
    assert attempt.status == VoiceAttemptStatus.PLACED.value
    assert attempt.to_number_masked and attempt.to_number_masked.endswith("1234")


def test_executor_records_awaiting_attempt_when_wait_for_outcome():
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="call_wait")):
        asyncio.run(executor.execute(_make_run(), _make_node(wait_for_outcome=True), {}))
    added = [c.args[0] for c in executor.session.add.call_args_list]
    attempts = [o for o in added if isinstance(o, WorkflowVoiceAttempt)]
    assert len(attempts) == 1
    assert attempts[0].status == VoiceAttemptStatus.AWAITING_OUTCOME.value


def test_executor_idempotent_when_already_placed():
    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(), already_placed=True
    )
    with _patch_client(result=RetellCallResult(call_id="x")) as call_mock:
        result = asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    assert result == "node-2"
    call_mock.assert_not_called()
    runtime.begin_step.assert_not_called()
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


# ---------------------------------------------------------------------------
# Wait-for-outcome park
# ---------------------------------------------------------------------------


def test_executor_parks_when_wait_for_outcome():
    executor, runtime, step = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="call_park")):
        result = asyncio.run(
            executor.execute(_make_run(), _make_node(wait_for_outcome=True), {})
        )
    assert isinstance(result, VoiceParked)
    assert result.step is step
    # Marked awaiting (NOT completed) with the placed-call marker + call_id.
    runtime.mark_step_awaiting_outcome.assert_called_once()
    mkw = runtime.mark_step_awaiting_outcome.call_args.kwargs
    assert mkw.get("result_code") == "call_placed_awaiting_outcome"
    assert mkw.get("result_metadata") == {"retell_call_id": "call_park"}
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


# ---------------------------------------------------------------------------
# P9 — crash-safe committed claim
# ---------------------------------------------------------------------------


def test_executor_commits_claim_before_placing_call():
    """A committed INITIATING claim is written (and the session committed) BEFORE the
    Retell POST, so a crash between POST and task-commit leaves a durable claim."""
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(result=RetellCallResult(call_id="c1")):
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    # A voice-attempt row was added and the session committed (the pre-POST claim).
    added = [c.args[0] for c in executor.session.add.call_args_list]
    assert any(isinstance(o, WorkflowVoiceAttempt) for o in added)
    executor.session.commit.assert_called()  # claim committed before the POST
    # Final state is placed (claim → placed after success).
    attempt = next(o for o in added if isinstance(o, WorkflowVoiceAttempt))
    assert attempt.status == VoiceAttemptStatus.PLACED.value


def test_executor_skips_when_committed_claim_exists():
    """Crash-tail redelivery: a committed non-FAILED claim → skip the dial entirely
    (at-most-once), without beginning a new step or placing a call."""
    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(), claim_id="existing-attempt",
    )
    with _patch_client(result=RetellCallResult(call_id="x")) as call_mock:
        result = asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    assert result == "node-2"
    call_mock.assert_not_called()      # no re-dial
    runtime.begin_step.assert_not_called()
    runtime.complete_step.assert_not_called()
    runtime.fail_run.assert_not_called()


def test_executor_transient_error_marks_claim_failed_for_retry():
    """A transient error marks the claim FAILED so the V-6 retry can re-dial (a FAILED
    claim is excluded from the skip check)."""
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(), attempt_number=1
    )
    with _patch_client(side_effect=RetellTransientError("retell_5xx: 503")):
        with pytest.raises(RetellTransientError):
            asyncio.run(executor.execute(_make_run(), _make_node(max_attempts=3), {}))
    added = [c.args[0] for c in executor.session.add.call_args_list]
    attempt = next(o for o in added if isinstance(o, WorkflowVoiceAttempt))
    assert attempt.status == VoiceAttemptStatus.FAILED.value


def test_executor_permanent_error_marks_claim_failed():
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(side_effect=RetellPermanentError("retell_4xx: 422")):
        asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    added = [c.args[0] for c in executor.session.add.call_args_list]
    attempt = next(o for o in added if isinstance(o, WorkflowVoiceAttempt))
    assert attempt.status == VoiceAttemptStatus.FAILED.value


def test_executor_ambiguous_timeout_fails_without_retry_and_keeps_claim_blocking():
    """XC-1b: a timeout/network error must NOT retry (the call may have been placed).
    Fail the run, do NOT re-raise, and leave the claim INITIATING (still blocking a
    redelivery re-dial) with an error_message — NOT marked FAILED."""
    from src.app.models.outbound_voice import VoiceAttemptStatus, WorkflowVoiceAttempt

    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(side_effect=RetellAmbiguousError("retell_network_error: TimeoutException")):
        result = asyncio.run(executor.execute(_make_run(), _make_node(max_attempts=3), {}))
    assert result == "node-2"          # returns (no re-raise → no Celery retry)
    runtime.fail_run.assert_called_once()
    assert "at-most-once" in _fail_reason(runtime)
    added = [c.args[0] for c in executor.session.add.call_args_list]
    attempt = next(o for o in added if isinstance(o, WorkflowVoiceAttempt))
    # Claim stays blocking (INITIATING, not FAILED) so a redelivery can't re-dial.
    assert attempt.status == VoiceAttemptStatus.INITIATING.value
    assert attempt.error_message and "network_error" in attempt.error_message


# ---------------------------------------------------------------------------
# Error classification (transient retry vs permanent fail)
# ---------------------------------------------------------------------------


def test_executor_permanent_error_fails_run():
    executor, runtime, _ = _make_executor(contact=_make_contact(), location=_make_location())
    with _patch_client(side_effect=RetellPermanentError("retell_4xx: 422")):
        result = asyncio.run(executor.execute(_make_run(), _make_node(), {}))
    assert result == "node-2"
    runtime.fail_step.assert_called_once()
    runtime.fail_run.assert_called_once()
    assert "send_voice error" in _fail_reason(runtime)


def test_executor_transient_error_reraises_for_retry():
    """A transient Retell error re-raises (so the Celery task retries) while attempts remain."""
    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(), attempt_number=1
    )
    with _patch_client(side_effect=RetellTransientError("retell_5xx: 503")):
        with pytest.raises(RetellTransientError):
            asyncio.run(executor.execute(_make_run(), _make_node(max_attempts=3), {}))
    runtime.fail_run.assert_not_called()  # not exhausted → retry, don't fail the run


def test_executor_transient_error_fails_run_when_attempts_exhausted():
    executor, runtime, _ = _make_executor(
        contact=_make_contact(), location=_make_location(), attempt_number=3
    )
    with _patch_client(side_effect=RetellTransientError("retell_5xx: 503")):
        result = asyncio.run(executor.execute(_make_run(), _make_node(max_attempts=3), {}))
    assert result == "node-2"
    runtime.fail_run.assert_called_once()
    assert "attempts exhausted" in _fail_reason(runtime)
