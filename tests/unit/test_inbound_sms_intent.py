"""STOP/HELP/START detection must catch the keyword anywhere in the body."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.twilio_webhooks import (
    _classify_confirmation_reply,
    _classify_intent,
    _mapped_sms_response_result,
    _verified_form,
    inbound_sms,
)


@pytest.mark.parametrize(
    "body,expected",
    [
        ("STOP", "STOP"),
        ("stop", "STOP"),
        ("STOP!", "STOP"),
        ("Please STOP calling me", "STOP"),
        ("please stop", "STOP"),
        ("UNSUBSCRIBE", "STOP"),
        ("cancel my notifications", "STOP"),
        ("END", "STOP"),
        ("START", "START"),
        ("Yes, START please", "START"),
        ("HELP", "HELP"),
        ("more info please", "HELP"),
        ("cancel my notifications", "STOP"),
        # STOP wins over START in the unlikely "STOP and START" case.
        ("STOP and START", "STOP"),
        # French / CASL opt-out keywords (accented and un-accented forms).
        ("ARRET", "STOP"),
        ("ARRÊT", "STOP"),
        ("Arrêt", "STOP"),
        ("arrêt s'il vous plaît", "STOP"),
        ("DÉSABONNER", "STOP"),
        ("DESABONNER", "STOP"),
        ("retirer", "STOP"),
        ("AIDE", "HELP"),
        ("aide", "HELP"),
        # No keyword token → empty.
        ("", ""),
        ("Thanks!", ""),
        ("STOPPING by tomorrow", ""),  # not a whole-word STOP
        ("CANCELLATION confirmed", ""),  # not a whole-word CANCEL
        ("cancel my appointment", ""),  # campaign handoff, not SMS opt-out
    ],
)
def test_classify_intent_finds_keywords_anywhere(body: str, expected: str) -> None:
    assert _classify_intent(body) == expected


@pytest.mark.parametrize("body", ["YES", "yes", "Y", "confirm", "C", "1", "1."])
def test_classify_confirmation_reply_accepts_bare_confirm_tokens(body: str) -> None:
    assert _classify_confirmation_reply(body) is True


@pytest.mark.parametrize(
    "body",
    ["", "yes but reschedule", "confirm and cancel", "oui", "cancel", "STOP", "11"],
)
def test_classify_confirmation_reply_rejects_ambiguous_or_non_confirm_tokens(body: str) -> None:
    assert _classify_confirmation_reply(body) is False


def test_mapped_response_with_context_preserves_staff_handoff_reason() -> None:
    result = _mapped_sms_response_result(
        SimpleNamespace(
            context_updates={"appointment_reminder_reply": "reschedule_requested"},
            handoff_reason="reschedule_requested",
        )
    )

    assert result is not None
    assert result.intent == "mapped_response"
    assert result.outcome == "staff_handoff_required"
    assert result.handoff_reason == "reschedule_requested"


# ---------------------------------------------------------------------------
# Webhook signature validation — sub-account auth token (Plan 10)
# ---------------------------------------------------------------------------


def _make_request(fields: dict, signature="sig"):
    form_data = MagicMock()
    form_data.multi_items = MagicMock(return_value=list(fields.items()))
    request = MagicMock()
    request.form = AsyncMock(return_value=form_data)
    request.headers = {"X-Twilio-Signature": signature}
    request.url = "https://api.example.com/twilio/webhooks/inbound-sms"
    return request


def _session_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_verified_form_validates_with_subaccount_token():
    """The signature is validated with the destination number's sub-account
    token, not the platform token."""
    request = _make_request({"To": "+16475550001", "From": "+14165551234", "Body": "hi"})
    captured = {}

    def _make_validator(token):
        captured["token"] = token
        v = MagicMock()
        v.validate = MagicMock(return_value=True)
        return v

    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_subaccount")

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        form = asyncio.run(_verified_form(request))

    assert form["To"] == "+16475550001"
    assert captured["token"] == "tok_subaccount"
    resolver.resolve_auth_token.assert_awaited_once_with("+16475550001", "+14165551234")


def test_verified_form_falls_back_to_platform_token():
    """When the number belongs to no sub-account the resolver returns the
    platform token and validation still succeeds."""
    request = _make_request({"To": "+16475550001", "From": "+14165551234", "Body": "hi"})
    captured = {}

    def _make_validator(token):
        captured["token"] = token
        v = MagicMock()
        v.validate = MagicMock(return_value=True)
        return v

    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_platform")

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        asyncio.run(_verified_form(request))

    assert captured["token"] == "tok_platform"


def test_verified_form_rejects_bad_signature():
    from fastapi import HTTPException

    request = _make_request({"To": "+16475550001", "From": "+14165551234"})
    resolver = MagicMock()
    resolver.resolve_auth_token = AsyncMock(return_value="tok_platform")

    def _make_validator(token):
        v = MagicMock()
        v.validate = MagicMock(return_value=False)
        return v

    with (
        patch("src.app.api.routes.twilio_webhooks.settings") as s,
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            return_value=_session_cm(AsyncMock()),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.TenantTwilioCredentialResolver",
            return_value=resolver,
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.RequestValidator",
            side_effect=_make_validator,
        ),
    ):
        s.twillio_api_secret = "tok_platform"
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(_verified_form(request))

    assert exc_info.value.status_code == 401


def test_confirm_reply_enqueues_resume_without_automatic_sms_acknowledgment():
    lookup_session = AsyncMock()
    tenant_session = AsyncMock()
    location = SimpleNamespace(id="loc-1", institution_id="inst-1", name="Clinic")
    inbound = SimpleNamespace(
        id="inbound-1",
        institution_id="inst-1",
        location_id="loc-1",
        workflow_run_id="run-1",
        conversation_thread_id="thread-1",
        contact_id="contact-1",
    )

    with (
        patch(
            "src.app.api.routes.twilio_webhooks._verified_form",
            new=AsyncMock(
                return_value={
                    "From": "+14165550100",
                    "To": "+14165550199",
                    "Body": "YES",
                    "MessageSid": "SM123",
                }
            ),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            side_effect=[_session_cm(lookup_session), _session_cm(tenant_session)],
        ),
        patch(
            "src.app.api.routes.twilio_webhooks._location_for_twilio_number",
            new=AsyncMock(return_value=location),
        ),
        patch(
            "src.app.services.automation.inbound_sms_routing_service.InboundSmsRoutingService.record_inbound",
            new=AsyncMock(return_value=inbound),
        ),
        patch(
            "src.app.services.automation.retell_sms_conversation_service."
            "RetellSmsConversationService.find_active_for_inbound",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.services.automation.campaign_conversation_service.CampaignConversationService.match_sms_response_mapping",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.services.automation.campaign_response_service.CampaignResponseService.record_sms_response",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
        patch("src.app.tasks.automation_workflow.resume_sms_confirmation") as resume,
    ):
        response = asyncio.run(inbound_sms(MagicMock()))

    resume.delay.assert_called_once()
    assert b"Thanks, we received your reply." not in response.body
    assert response.body == b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def test_mapped_handoff_reply_notifies_staff_and_enqueues_resume():
    lookup_session = AsyncMock()
    tenant_session = AsyncMock()
    location = SimpleNamespace(id="loc-1", institution_id="inst-1", name="Clinic")
    inbound = SimpleNamespace(
        id="inbound-1",
        institution_id="inst-1",
        location_id="loc-1",
        workflow_run_id="run-1",
        conversation_thread_id="thread-1",
        contact_id="contact-1",
    )
    handoff = SimpleNamespace(id="handoff-1")
    record_response = AsyncMock(return_value=(MagicMock(), handoff))
    mapping_match = SimpleNamespace(
        mapping=SimpleNamespace(
            context_updates={"appointment_reminder_reply": "reschedule_requested"},
            handoff_reason="reschedule_requested",
        )
    )

    with (
        patch(
            "src.app.api.routes.twilio_webhooks._verified_form",
            new=AsyncMock(
                return_value={
                    "From": "+14165550100",
                    "To": "+14165550199",
                    "Body": "R",
                    "MessageSid": "SM123",
                }
            ),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            side_effect=[_session_cm(lookup_session), _session_cm(tenant_session)],
        ),
        patch(
            "src.app.api.routes.twilio_webhooks._location_for_twilio_number",
            new=AsyncMock(return_value=location),
        ),
        patch(
            "src.app.services.automation.inbound_sms_routing_service.InboundSmsRoutingService.record_inbound",
            new=AsyncMock(return_value=inbound),
        ),
        patch(
            "src.app.services.automation.retell_sms_conversation_service."
            "RetellSmsConversationService.find_active_for_inbound",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.services.automation.campaign_conversation_service.CampaignConversationService.match_sms_response_mapping",
            new=AsyncMock(return_value=mapping_match),
        ),
        patch(
            "src.app.services.automation.campaign_response_service.CampaignResponseService.record_sms_response",
            new=record_response,
        ),
        patch(
            "src.app.tasks.in_app_notifications.enqueue_in_app_notifications",
        ) as notify,
        patch("src.app.tasks.automation_workflow.resume_sms_confirmation") as resume,
    ):
        response = asyncio.run(inbound_sms(MagicMock()))

    parsed = record_response.await_args.kwargs["parsed"]
    assert parsed.intent == "mapped_response"
    assert parsed.outcome == "staff_handoff_required"
    assert parsed.handoff_reason == "reschedule_requested"
    notify.assert_called_once()
    assert notify.call_args.kwargs["data"]["campaign_staff_handoff_id"] == "handoff-1"
    resume.delay.assert_called_once()
    assert response.body == b'<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def test_stop_suppresses_and_cancels_correlated_run_before_one_commit():
    lookup_session = AsyncMock()
    tenant_session = AsyncMock()
    location = SimpleNamespace(id="loc-1", institution_id="inst-1", name="Clinic")
    inbound = SimpleNamespace(
        id="inbound-1",
        institution_id="inst-1",
        location_id="loc-1",
        workflow_run_id="run-1",
        conversation_thread_id="thread-1",
        contact_id="contact-1",
    )
    compliance = AsyncMock()
    opt_out = AsyncMock()
    operations: list[str] = []

    async def suppress(**_kwargs):
        operations.append("suppress")

    async def cancel(**_kwargs):
        operations.append("cancel")
        return 1

    async def commit():
        operations.append("commit")

    compliance.suppress.side_effect = suppress
    opt_out.cancel_active_sms_runs.side_effect = cancel
    tenant_session.commit.side_effect = commit

    with (
        patch(
            "src.app.api.routes.twilio_webhooks._verified_form",
            new=AsyncMock(
                return_value={
                    "From": "+14165550100",
                    "To": "+14165550199",
                    "Body": "STOP",
                    "MessageSid": "SM123",
                }
            ),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.get_system_db_session",
            side_effect=[_session_cm(lookup_session), _session_cm(tenant_session)],
        ),
        patch(
            "src.app.api.routes.twilio_webhooks._location_for_twilio_number",
            new=AsyncMock(return_value=location),
        ),
        patch(
            "src.app.services.automation.inbound_sms_routing_service."
            "InboundSmsRoutingService.record_inbound",
            new=AsyncMock(return_value=inbound),
        ),
        patch(
            "src.app.services.automation.retell_sms_conversation_service."
            "RetellSmsConversationService.find_active_for_inbound",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "src.app.services.automation.campaign_response_service."
            "CampaignResponseService.record_sms_response",
            new=AsyncMock(return_value=(MagicMock(), None)),
        ),
        patch(
            "src.app.api.routes.twilio_webhooks.SmsComplianceService",
            return_value=compliance,
        ) as compliance_cls,
        patch(
            "src.app.api.routes.twilio_webhooks.SmsOptOutWorkflowService",
            return_value=opt_out,
        ) as opt_out_cls,
        patch(
            "src.app.api.routes.twilio_webhooks._audit_keyword",
            new=AsyncMock(),
        ),
    ):
        asyncio.run(inbound_sms(MagicMock()))

    compliance_cls.assert_called_once_with(tenant_session)
    opt_out_cls.assert_called_once_with(tenant_session)
    opt_out.cancel_active_sms_runs.assert_awaited_once_with(
        institution_id="inst-1",
        location_id="loc-1",
        phone="+14165550100",
        correlated_run_id="run-1",
    )
    assert operations == ["suppress", "cancel", "commit"]
