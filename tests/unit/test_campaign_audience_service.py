"""Unit tests for campaign audience preview and enrollment (Plan 07)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.automation.audience_service import (
    AudienceSegment,
    CampaignAudienceService,
)


def _workflow():
    return SimpleNamespace(
        id="wf-1",
        current_version_id="ver-1",
        location_id="loc-1",
        trigger_type="manual",
        definition={
            "trigger": {"type": "manual"},
            "entry_node_id": "sms-1",
            "nodes": [
                {
                    "type": "send_sms",
                    "id": "sms-1",
                    "body_template": "Hi {{patient_first_name}}",
                    "next_node_id": "exit-1",
                },
                {"type": "exit", "id": "exit-1", "outcome": "done"},
            ],
            "compliance": {"content_class": "transactional_care", "consent_required": True},
        },
    )


def _contact(contact_id: str, *, phone: str | None = "+15550101010"):
    return SimpleNamespace(
        id=contact_id,
        full_name=f"Patient {contact_id}",
        first_name="Patient",
        last_name=contact_id,
        phone=phone,
        email=None,
        nexhealth_patient_id=None,
    )


class _PreviewService(CampaignAudienceService):
    async def _candidate_contacts(self, *, institution_id: str, location_ids: list[str]):
        return [_contact("c-1"), _contact("c-2")]

    async def _exclusion_reasons(self, contact, **kwargs):
        if str(contact.id) == "c-2":
            return ["do_not_contact", "suppressed"]
        return []


@pytest.mark.asyncio
async def test_preview_returns_counts_reason_breakdown_and_masked_samples():
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    preview = await _PreviewService(session).preview(
        _workflow(),
        institution_id="inst-1",
        segment=AudienceSegment(),
        actor_user_id="user-1",
    )

    assert preview.included_count == 1
    assert preview.excluded_count == 1
    assert preview.counts_by_reason == {"do_not_contact": 1, "suppressed": 1}
    assert {sample.status for sample in preview.samples} == {"included", "excluded"}
    assert all(sample.phone_masked != "+15550101010" for sample in preview.samples)
    session.add.assert_called_once()


class _EnrollService(CampaignAudienceService):
    async def preview(self, *args, **kwargs):
        return SimpleNamespace(
            preview_id="prev-1",
            included_count=2,
            counts_by_reason={"already_booked": 1},
        )

    async def _included_contact_ids(self, *args, **kwargs):
        return ["c-1", "c-2"]


@pytest.mark.asyncio
async def test_enqueue_enrollment_uses_preview_id_for_idempotency():
    session = AsyncMock()
    task = MagicMock()

    with patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run.apply_async",
        task,
    ):
        result = await _EnrollService(session).enqueue_enrollment(
            _workflow(),
            institution_id="inst-1",
            segment=AudienceSegment(),
            actor_user_id="user-1",
            max_enrollments=500,
        )

    assert result.enqueued == 2
    assert result.skipped == 0
    assert task.call_count == 2
    keys = [call.kwargs["kwargs"]["idempotency_key"] for call in task.call_args_list]
    assert keys == ["audience:prev-1:c-1", "audience:prev-1:c-2"]
