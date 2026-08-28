from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from src.app.models.contact import Contact
from src.app.models.institution_location import InstitutionLocation
from src.app.services.automation.retell_sms_conversation_service import (
    RetellSmsConversationService,
)
from src.app.services.automation.retell_sms_policy import RETELL_SMS_POLICY


def test_platform_policy_is_fixed_outside_workflow_definition() -> None:
    assert RETELL_SMS_POLICY.inactivity_timeout_seconds == 3600
    assert RETELL_SMS_POLICY.max_duration_seconds == 86400
    assert RETELL_SMS_POLICY.max_patient_turns == 12
    assert RETELL_SMS_POLICY.max_response_segments == 3


def test_dynamic_variables_normalize_provider_context_automatically() -> None:
    contact = SimpleNamespace(
        first_name="Jordan",
        last_name="Rivera",
        full_name="Jordan Rivera",
    )
    location = SimpleNamespace(
        name="Downtown Dental",
        phone="+14165551234",
        timezone="America/Toronto",
        address="100 Main St",
        city="Toronto",
        state="ON",
    )
    previous = SimpleNamespace(body="Please confirm your appointment")
    result = MagicMock()
    result.scalar_one_or_none.return_value = previous
    db = MagicMock()
    db.get = AsyncMock(
        side_effect=lambda model, _identifier: (
            contact
            if model is Contact
            else location
            if model is InstitutionLocation
            else None
        )
    )
    db.execute = AsyncMock(return_value=result)
    retell_session = SimpleNamespace(
        contact_id="contact-1",
        location_id="location-1",
        conversation_thread_id="thread-1",
    )

    variables = asyncio.run(
        RetellSmsConversationService(db).dynamic_variables(
            retell_session=retell_session,
            context={
                "campaign_goal": "Confirm the upcoming appointment",
                "patient": {"preferred_language": "English"},
                "appointment": {
                    "start_time": "2026-08-28T15:30:00-04:00",
                    "status": "confirmed",
                    "reason": "Cleaning",
                    "appointment_type_name": "Hygiene",
                    "provider_name": "Dr. Smith",
                },
            },
        )
    )

    assert variables["patient_first_name"] == "Jordan"
    assert variables["patient_preferred_language"] == "English"
    assert variables["clinic_name"] == "Downtown Dental"
    assert variables["clinic_phone"] == "+14165551234"
    assert variables["clinic_timezone"] == "America/Toronto"
    assert variables["appointment_date"] == "August 28, 2026"
    assert variables["appointment_time"] == "3:30 PM"
    assert variables["appointment_status"] == "confirmed"
    assert variables["appointment_reason"] == "Cleaning"
    assert variables["appointment_type"] == "Hygiene"
    assert variables["provider_name"] == "Dr. Smith"
    assert variables["conversation_goal"] == "Confirm the upcoming appointment"
    assert variables["previous_sms_message"] == "Please confirm your appointment"
    assert "provider_id" not in variables
    assert "appointment_id" not in variables
