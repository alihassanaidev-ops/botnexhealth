"""Item 16 · once a patient answers, the engine stops the rest of the run.

The protection previously depended on whoever drew the campaign remembering to
branch on the reply. A campaign that omitted the branch got none — the patient
confirmed by text and was still phoned an hour later by a step scheduled before
they answered. The rule now lives in the compliance gate, so a campaign author
cannot create the problem by omission.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.compliance_gate_service import ComplianceGateService


def _run(contact_id="c-1", location_id=None):
    run = MagicMock()
    run.id = "run-1"
    run.institution_id = "inst-1"
    run.location_id = location_id
    run.contact_id = contact_id
    return run


def _gate(*, responded: bool, halted: bool = False):
    """A gate whose response lookup and halt lookup are both stubbed."""
    session = AsyncMock()
    service = ComplianceGateService(session)
    service._active_halt = AsyncMock(return_value=halted)
    service._has_responded = AsyncMock(return_value=responded)
    # Anything past the response check would need real consent data; the tests
    # below only assert on what happens at or before it.
    service._check_sms = AsyncMock(return_value=MagicMock(action="allow"))
    return service


def _check(service, channel="send_voice", **kwargs):
    return asyncio.run(_run_check(service, channel, **kwargs))


async def _run_check(service, channel, **kwargs):
    return await service.check(_run(), channel, **kwargs)


@pytest.mark.parametrize("channel", ["send_voice", "send_email", "send_sms"])
def test_a_patient_who_answered_is_not_contacted_again_on_any_channel(channel):
    result = _check(_gate(responded=True), channel)
    assert result.action == "block"
    assert result.reason == "patient_responded"


def test_the_rule_applies_without_the_campaign_drawing_a_branch():
    """No campaign configuration is consulted — this is engine behaviour."""
    service = _gate(responded=True)
    result = _check(service, "send_voice")
    assert result.action == "block"
    service._has_responded.assert_awaited_once()


def test_a_campaign_can_deliberately_opt_out():
    result = _check(_gate(responded=True), "send_sms", continue_after_response=True)
    assert result.action != "block" or result.reason != "patient_responded"


def test_a_run_with_no_response_is_unaffected():
    service = _gate(responded=False)
    result = _check(service, "send_sms")
    assert not (result.action == "block" and result.reason == "patient_responded")


def test_emergency_halt_still_wins():
    """The kill switch is checked first and must not be reordered behind this."""
    result = _check(_gate(responded=False, halted=True), "send_sms")
    assert result.action == "block"
    assert result.reason == "emergency_halt"


def test_response_is_checked_before_quiet_hours():
    """Holding a message that must never be sent only delays it.

    A run inside quiet hours whose patient has answered must block outright
    rather than come back for a hold.
    """
    service = _gate(responded=True)
    run = _run(location_id="loc-1")
    with patch(
        "src.app.services.automation.compliance_gate_service.QuietHoursService"
    ) as MockQuiet:
        MockQuiet.return_value.is_quiet_hours = AsyncMock(return_value=True)
        result = asyncio.run(service.check(run, "send_sms"))
    assert result.action == "block"
    assert result.reason == "patient_responded"
    MockQuiet.return_value.is_quiet_hours.assert_not_awaited()
