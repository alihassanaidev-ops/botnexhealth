"""Workflow enrollment dispatch for landed sales enquiries."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from src.app.services.automation.enquiry_trigger_service import (
    EnquiryTriggerService,
    EnquiryWorkflowDispatch,
    enqueue_enquiry_workflow_dispatches,
    enquiry_trigger_context,
    make_enquiry_idempotency_key,
    workflow_matches_enquiry,
)


#: A landed enquiry is now the `enquiry.received` key on the event trigger.
_ENQUIRY_TRIGGER = {"type": "event", "event_keys": ["enquiry.received"]}


def _definition(trigger: dict | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "triggers": [trigger or dict(_ENQUIRY_TRIGGER)],
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }


def _workflow(
    *,
    wf_id: str,
    location_id: str | None = "loc-1",
    definition: dict | None = None,
):
    definition = definition or _definition()
    trigger = definition["triggers"][0]
    workflow = MagicMock()
    workflow.id = wf_id
    workflow.institution_id = "inst-1"
    workflow.location_id = location_id
    workflow.current_version_id = f"ver-{wf_id}"
    workflow.definition = definition
    # The shared lookup reads these model properties; a MagicMock cannot derive
    # them from `definition` the way AutomationWorkflow does.
    workflow.trigger_type = trigger["type"]
    workflow.trigger_types = [trigger["type"]]
    workflow.subscribed_event_keys = list(trigger.get("event_keys") or [])
    return workflow


def _session(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


def _contact(**overrides):
    contact = SimpleNamespace(
        id="lead-1",
        location_id="loc-1",
        lead_source="website_form",
        lead_status="new",
        intake_key="form-response-1",
        external_ref="typeform-response-1",
        nexhealth_patient_id=None,
        email="dana@example.com",
        phone="+15551234567",
    )
    for key, value in overrides.items():
        setattr(contact, key, value)
    return contact


def test_context_is_normalized_and_does_not_include_contact_details() -> None:
    context = enquiry_trigger_context(
        contact=_contact(nexhealth_patient_id="nh-1"),
        location_id="loc-1",
        intake_key="form-response-1",
        source="website_form",
        created=False,
        matched_existing_contact=True,
    )

    assert context["event"] == "enquiry.received"
    assert context["trigger_type"] == "enquiry_received"
    assert context["contact_id"] == "lead-1"
    assert context["location_id"] == "loc-1"
    assert context["enquiry_source"] == "website_form"
    assert context["enquiry"]["external_ref"] == "typeform-response-1"
    assert context["matched_existing_contact"] is True
    assert context["patient_id"] == "nh-1"
    assert "email" not in context
    assert "phone" not in context
    assert "email" not in context["enquiry"]
    assert "phone" not in context["enquiry"]


def test_prepare_dispatches_matches_location_and_trigger_filter() -> None:
    matching_filter = {
        **_ENQUIRY_TRIGGER,
        "filter": {
            "kind": "rule",
            "field": "enquiry_source",
            "op": "eq",
            "value": "website_form",
        },
    }
    wrong_filter = {
        **_ENQUIRY_TRIGGER,
        "filter": {
            "kind": "rule",
            "field": "enquiry_source",
            "op": "eq",
            "value": "partner_referral",
        },
    }
    session = _session(
        [
            _workflow(
                wf_id="here",
                location_id="loc-1",
                definition=_definition(matching_filter),
            ),
            _workflow(
                wf_id="all",
                location_id=None,
                definition=_definition(matching_filter),
            ),
            _workflow(
                wf_id="away",
                location_id="loc-2",
                definition=_definition(matching_filter),
            ),
            _workflow(
                wf_id="wrong-filter",
                location_id="loc-1",
                definition=_definition(wrong_filter),
            ),
            _workflow(
                wf_id="manual",
                location_id="loc-1",
                definition=_definition({"type": "manual"}),
            ),
        ]
    )

    dispatches = asyncio.run(
        EnquiryTriggerService(session).prepare_dispatches(
            institution_id="inst-1",
            location_id="loc-1",
            contact=_contact(),
            intake_key="form-response-1",
            source="website_form",
            created=True,
            matched_existing_contact=False,
        )
    )

    assert [dispatch.workflow_id for dispatch in dispatches] == ["here", "all"]
    assert [dispatch.location_id for dispatch in dispatches] == ["loc-1", "loc-1"]
    assert dispatches[0].task_kwargs()["trigger_type"] == "enquiry_received"
    assert dispatches[0].task_kwargs()["trigger_ref_type"] == "contact"
    assert dispatches[0].task_kwargs()["trigger_ref_id"] == "lead-1"
    assert dispatches[0].trigger_metadata["enquiry_source"] == "website_form"


def test_prepare_dispatches_does_not_match_scoped_workflow_without_event_location() -> (
    None
):
    session = _session(
        [
            _workflow(wf_id="scoped", location_id="loc-1"),
            _workflow(wf_id="institution-wide", location_id=None),
        ]
    )

    dispatches = asyncio.run(
        EnquiryTriggerService(session).prepare_dispatches(
            institution_id="inst-1",
            location_id=None,
            contact=_contact(location_id=None),
            intake_key=None,
            source="manual",
            created=True,
            matched_existing_contact=False,
        )
    )

    assert [dispatch.workflow_id for dispatch in dispatches] == ["institution-wide"]
    assert dispatches[0].location_id is None


def test_workflow_matches_enquiry_rejects_other_trigger_definitions() -> None:
    workflow = _workflow(
        wf_id="manual",
        definition=_definition({"type": "manual"}),
    )

    assert (
        workflow_matches_enquiry(workflow, {"enquiry_source": "website_form"}) is False
    )


def test_idempotency_key_is_per_workflow_version_contact_and_intake_submission() -> (
    None
):
    first = make_enquiry_idempotency_key("ver-1", "lead-1", intake_key="response-1")

    assert first == make_enquiry_idempotency_key(
        "ver-1", "lead-1", intake_key="response-1"
    )
    assert first != make_enquiry_idempotency_key(
        "ver-1", "lead-1", intake_key="response-2"
    )
    assert make_enquiry_idempotency_key("ver-1", "lead-1") == (
        make_enquiry_idempotency_key("ver-1", "lead-1", intake_key=None)
    )


def test_enqueue_enquiry_dispatches_uses_workflow_queue() -> None:
    dispatch = EnquiryWorkflowDispatch(
        institution_id="inst-1",
        workflow_id="wf-1",
        workflow_version_id="ver-1",
        contact_id="lead-1",
        location_id="loc-1",
        trigger_ref_id="lead-1",
        idempotency_key="enquiry:ver-1:lead-1:digest",
        trigger_metadata={"enquiry_source": "website_form"},
    )

    with patch(
        "src.app.tasks.automation_workflow.enroll_and_start_workflow_run"
    ) as task:
        task.apply_async = MagicMock()

        count = enqueue_enquiry_workflow_dispatches([dispatch])

    assert count == 1
    task.apply_async.assert_called_once_with(
        kwargs=dispatch.task_kwargs(),
        queue="workflow",
    )
