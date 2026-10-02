"""The confirmation email sent after a patient books through a link.

Reuses the voice agent's PATIENT_APPOINTMENT_CONFIRMATION template so a clinic
edits that wording once and both channels follow it. What is asserted here is
mostly the gating: an email must never be the reason a real booking is lost, and
must never go to an address the patient could have supplied themselves.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.api.routes.campaign_booking import _send_booking_confirmation_email


def _template(active=True):
    t = MagicMock()
    t.is_active = active
    return t


def _adapter(email="dana@example.com"):
    patient = MagicMock()
    patient.email = email
    patient.first_name = "Dana"
    patient.last_name = "Reyes"
    adapter = MagicMock()
    adapter.get_patient = AsyncMock(return_value=patient)
    return adapter


def _contact(pms_id="nh-1"):
    c = MagicMock()
    c.nexhealth_patient_id = pms_id
    return c


async def _run_helper(*, template, adapter, contact, action="book"):
    location = MagicMock()
    location.name = "Olive Tree Dental"
    svc = MagicMock()
    svc.get_template_by_type = AsyncMock(return_value=template)
    sender = AsyncMock()

    with patch(
        "src.app.services.email_template_service.EmailTemplateService",
        return_value=svc,
    ), patch(
        "src.app.services.email_notification_service.EmailNotificationService"
    ) as notif:
        notif.return_value.send_notification = sender
        await _send_booking_confirmation_email(
            session=MagicMock(),
            institution_id="inst-1",
            location=location,
            contact=contact,
            adapter=adapter,
            run_id="run-1",
            appointment_start="2026-09-02T10:00:00-04:00",
            provider_name="Dr. Kadri",
            action=action,
        )
    return sender


@pytest.mark.asyncio
class TestGating:
    async def test_nothing_is_sent_when_the_clinic_has_not_activated_the_template(self):
        """Same gate the voice agent's confirmation uses."""
        sender = await _run_helper(
            template=_template(active=False), adapter=_adapter(), contact=_contact()
        )
        sender.assert_not_awaited()

    async def test_nothing_is_sent_when_the_template_does_not_exist(self):
        sender = await _run_helper(
            template=None, adapter=_adapter(), contact=_contact()
        )
        sender.assert_not_awaited()

    async def test_nothing_is_sent_without_a_pms_record(self):
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact(pms_id=None)
        )
        sender.assert_not_awaited()

    async def test_nothing_is_sent_when_the_pms_holds_no_email(self):
        sender = await _run_helper(
            template=_template(), adapter=_adapter(email=""), contact=_contact()
        )
        sender.assert_not_awaited()


@pytest.mark.asyncio
class TestContent:
    async def test_it_goes_to_the_address_on_file_in_the_practice_software(self):
        """Not to anything typed into the page: a forwarded link must not be
        able to redirect someone else's confirmation."""
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact()
        )
        assert sender.await_args.kwargs["recipients"] == ["dana@example.com"]

    async def test_it_carries_the_time_the_pms_actually_wrote(self):
        """The call path sends a transcribed phrase; a link knows the real value."""
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact()
        )
        payload = sender.await_args.kwargs["payload"]
        assert payload["appointment_datetime"] == "2026-09-02T10:00:00-04:00"
        assert payload["appointment_provider"] == "Dr. Kadri"
        assert payload["location_name"] == "Olive Tree Dental"

    async def test_it_prefers_the_name_the_practice_software_holds(self):
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact()
        )
        assert sender.await_args.kwargs["payload"]["appointment_patient_name"] == "Dana Reyes"

    async def test_it_is_marked_patient_facing(self):
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact()
        )
        assert sender.await_args.kwargs["patient_facing"] is True


@pytest.mark.asyncio
class TestIdempotency:
    async def test_reopening_the_link_cannot_send_a_second_confirmation(self):
        sender = await _run_helper(
            template=_template(), adapter=_adapter(), contact=_contact()
        )
        assert sender.await_args.kwargs["idempotency_key"] == "campaign-link:run-1:book"

    async def test_a_later_reschedule_sends_its_own(self):
        """Scoped by action, so changing the appointment still confirms."""
        sender = await _run_helper(
            template=_template(),
            adapter=_adapter(),
            contact=_contact(),
            action="reschedule",
        )
        assert (
            sender.await_args.kwargs["idempotency_key"]
            == "campaign-link:run-1:reschedule"
        )
