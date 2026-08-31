"""Unit tests for ChannelReadinessService (Plan 10).

Readiness is computed from existing creds. A channel that cannot send at all is
an error and blocks publishing (Item 13); the email platform-address fallback
stays a warning, because mail from that address does deliver. Covers SMS ready
vs. not, email ready vs. not, voice (node retell_agent_id), and the
null-location short-circuit (institution/template context → no issues).
"""

from __future__ import annotations

from src.app.services.automation.validation_service import ValidationIssue

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.models.institution import Institution
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.channel_readiness import ChannelReadinessService
from src.app.services.automation.definition_schema import WorkflowDefinition


# ---------------------------------------------------------------------------
# Definition builders
# ---------------------------------------------------------------------------


def _def(*nodes: dict, entry: str | None = None) -> WorkflowDefinition:
    node_list = list(nodes) + [{"type": "exit", "id": "x1", "outcome": "done"}]
    return WorkflowDefinition.model_validate(
        {
            "trigger": {"type": "manual"},
            "entry_node_id": entry or node_list[0]["id"],
            "nodes": node_list,
        }
    )


def _sms_def():
    return _def(
        {"type": "send_sms", "id": "s1", "body_template": "hi", "next_node_id": "x1"}
    )


def _email_def():
    return _def(
        {
            "type": "send_email",
            "id": "e1",
            "subject_template": "hi",
            "body_template": "body",
            "next_node_id": "x1",
        }
    )


def _voice_def(agent_id="agent-123"):
    return _def(
        {
            "type": "send_voice",
            "id": "v1",
            "retell_agent_id": agent_id,
            "next_node_id": "x1",
        }
    )


def _exit_only_def():
    return _def(entry="x1")


# ---------------------------------------------------------------------------
# Session / model mocks
# ---------------------------------------------------------------------------


def _make_location(from_number="+16475550001", retell_agent_id=None, retell_from_number="+16475550002"):
    loc = MagicMock(spec=InstitutionLocation)
    loc.twilio_from_number = from_number
    loc.retell_agent_id = retell_agent_id
    loc.retell_from_number = retell_from_number
    return loc


def _make_institution(sid=None, token=None, email_from=None):
    inst = MagicMock(spec=Institution)
    inst.twilio_account_sid = sid
    inst.twilio_auth_token = token
    inst.email_from_address = email_from
    inst.email_from_name = None
    return inst


def _make_session(location=None, institution=None, profile_exists=False):
    session = AsyncMock()

    async def _get(model, pk):
        if model is InstitutionLocation:
            return location
        if model is Institution:
            return institution
        return None

    session.get = AsyncMock(side_effect=_get)
    exec_result = MagicMock()
    exec_result.scalar = MagicMock(return_value="profile-1" if profile_exists else None)
    exec_result.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=exec_result)
    return session


def _check(definition, session, *, institution_id="inst-1", location_id="loc-1"):
    svc = ChannelReadinessService(session)
    return asyncio.run(
        svc.check(definition, institution_id=institution_id, location_id=location_id)
    )


# ---------------------------------------------------------------------------
# Null location → no issues (institution/template context)
# ---------------------------------------------------------------------------


def test_null_location_returns_no_issues():
    issues = _check(_sms_def(), _make_session(), location_id=None)
    assert issues == []


def test_no_channel_nodes_returns_no_issues():
    session = _make_session(location=_make_location(from_number=None))
    issues = _check(_exit_only_def(), session)
    assert issues == []


# ---------------------------------------------------------------------------
# SMS readiness
# ---------------------------------------------------------------------------


def test_sms_ready_when_from_number_and_platform_creds():
    session = _make_session(location=_make_location(from_number="+16475550001"))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        issues = _check(_sms_def(), session)
    assert issues == []


def test_sms_not_ready_without_from_number():
    session = _make_session(location=_make_location(from_number=None))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        issues = _check(_sms_def(), session)
    assert len(issues) == 1
    # Item 13: there is no sender to send with, so every enrolled patient's run
    # would fail at this step. That blocks publishing rather than advising.
    assert issues[0].severity == "error"
    assert issues[0].code == "channel_not_ready"
    assert issues[0].node_id == "s1"


def test_sms_not_ready_without_any_credentials():
    session = _make_session(location=_make_location(from_number="+16475550001"))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = None
        s.twillio_api_secret = None
        issues = _check(_sms_def(), session)
    assert len(issues) == 1
    assert issues[0].code == "channel_not_ready"


def test_sms_not_ready_with_placeholder_platform_credentials():
    session = _make_session(location=_make_location(from_number="+16475550001"))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        s.twillio_api_secret = "your-twilio-auth-token"
        issues = _check(_sms_def(), session)
    assert len(issues) == 1
    assert issues[0].code == "channel_not_ready"


def test_sms_ready_with_institution_subaccount_no_platform():
    inst = _make_institution(sid="AC_sub", token="tok_sub")
    session = _make_session(location=_make_location(), institution=inst)
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = None
        s.twillio_api_secret = None
        issues = _check(_sms_def(), session)
    assert issues == []


# ---------------------------------------------------------------------------
# Email readiness
# ---------------------------------------------------------------------------


def test_email_ready_with_platform_from_and_api_key():
    session = _make_session(location=_make_location(), institution=_make_institution())
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.resend_from_email = "platform@example.com"
        cs.resend_api_key = "re_test"
        issues = _check(_email_def(), session)
    assert issues == []


def test_email_not_ready_without_from_address():
    session = _make_session(location=_make_location(), institution=_make_institution())
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.resend_from_email = None
        cs.resend_api_key = "re_test"
        issues = _check(_email_def(), session)
    assert len(issues) == 1
    assert issues[0].node_id == "e1"
    assert issues[0].code == "channel_not_ready"


def test_email_not_ready_without_api_key():
    session = _make_session(location=_make_location(), institution=_make_institution())
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.resend_from_email = "platform@example.com"
        cs.resend_api_key = None
        issues = _check(_email_def(), session)
    assert len(issues) == 1


def test_email_not_ready_with_placeholder_platform_credentials():
    session = _make_session(location=_make_location(), institution=_make_institution())
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.resend_from_email = "alerts@yourdomain.com"
        cs.resend_api_key = "re_xxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
        issues = _check(_email_def(), session)
    assert len(issues) == 1
    assert issues[0].code == "channel_not_ready"


# ---------------------------------------------------------------------------
# Voice readiness (retell_agent_id carried on the node)
# ---------------------------------------------------------------------------


def test_voice_configurable_when_node_has_agent():
    session = _make_session(location=_make_location())
    issues = _check(_voice_def(agent_id="agent-123"), session)
    assert issues == []


# ---------------------------------------------------------------------------
# readiness_for_location — endpoint helper
# ---------------------------------------------------------------------------


def test_readiness_for_location_reports_all_channels():
    inst = _make_institution(email_from="clinic@example.com")
    loc = _make_location(from_number="+16475550001", retell_agent_id="agent-1")
    session = _make_session(location=loc, institution=inst, profile_exists=True)
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.twillio_sid = "AC_platform"
        ms.twillio_api_secret = "tok_platform"
        ms.resend_from_email = "platform@example.com"
        cs.resend_api_key = "re_test"
        svc = ChannelReadinessService(session)
        report = asyncio.run(
            svc.readiness_for_location(institution_id="inst-1", location_id="loc-1")
        )
    assert report.sms is True
    assert report.email is True
    assert report.voice_configurable is True
    assert len(report.details) == 3
    assert {d["channel"] for d in report.details} == {"sms", "email", "voice"}


def test_readiness_for_location_flags_missing_setup():
    loc = _make_location(from_number=None, retell_agent_id=None)
    session = _make_session(location=loc, institution=_make_institution())
    with patch("src.app.services.messaging_credentials.settings") as ms, patch(
        "src.app.services.automation.channel_readiness.settings"
    ) as cs:
        ms.twillio_sid = None
        ms.twillio_api_secret = None
        ms.resend_from_email = None
        cs.resend_api_key = None
        svc = ChannelReadinessService(session)
        report = asyncio.run(
            svc.readiness_for_location(institution_id="inst-1", location_id="loc-1")
        )
    assert report.sms is False
    assert report.email is False
    assert report.voice_configurable is False
    assert all(d["reason"] for d in report.details)


# ---------------------------------------------------------------------------
# Item 13 · a declared requirement that cannot be met blocks publishing
# ---------------------------------------------------------------------------


def test_unsendable_sms_blocks_publishing():
    """A campaign that needs texts, on a clinic with no sender, must not publish.

    Publishing is fail-closed on error severity, so this is the whole control:
    filed as a warning it published happily and then failed silently for every
    patient it enrolled.
    """
    from src.app.services.automation.validation_service import WorkflowValidationService

    session = _make_session(location=_make_location(from_number=None))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = None
        s.twillio_api_secret = None
        issues = _check(_sms_def(), session)
    assert not WorkflowValidationService.is_publishable(issues)


def test_the_failure_names_the_requirement_and_what_to_do():
    session = _make_session(location=_make_location(from_number=None))
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = None
        s.twillio_api_secret = None
        issues = _check(_sms_def(), session)
    message = issues[0].message.lower()
    assert "sms" in message
    assert "twilio" in message and "sender number" in message


def test_a_ready_campaign_still_publishes():
    from src.app.services.automation.validation_service import WorkflowValidationService

    session = _make_session(location=_make_location())
    with patch("src.app.services.messaging_credentials.settings") as s:
        s.twillio_sid = "AC_platform"
        s.twillio_api_secret = "tok_platform"
        issues = _check(_sms_def(), session)
    assert issues == []
    assert WorkflowValidationService.is_publishable(issues)


def test_advisory_warnings_still_do_not_block():
    """The email platform-address fallback delivers, so it stays advice."""
    from src.app.services.automation.validation_service import WorkflowValidationService

    assert WorkflowValidationService.is_publishable(
        [
            ValidationIssue(
                severity="warning", message="falls back to the platform address"
            )
        ]
    )
