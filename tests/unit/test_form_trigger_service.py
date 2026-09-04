"""Which workflows a submitted form starts.

The selection is what the whiteboard asked for — pick the form type, then the
specific form — so the tests are about that narrowing being real: a workflow
watching the Typeform "ABC" form must not enroll from a Meta lead, or from the
practice's other Typeform.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.app.services.automation.form_trigger_service import (
    FormTriggerService,
    make_form_idempotency_key,
    workflow_matches_submission,
)


def _definition(trigger: dict | None = None) -> dict:
    return {
        "schema_version": "1.0",
        "trigger": trigger or {"type": "form_submitted"},
        "entry_node_id": "exit-1",
        "nodes": [{"type": "exit", "id": "exit-1", "outcome": "done"}],
    }


def _workflow(*, wf_id: str = "wf-1", definition: dict | None = None, location_id="loc-1"):
    workflow = MagicMock()
    workflow.id = wf_id
    workflow.institution_id = "inst-1"
    workflow.location_id = location_id
    workflow.current_version_id = f"ver-{wf_id}"
    # Read from the version's definition JSON in production; set here because
    # the lookup filters on it before any trigger-specific matching runs.
    workflow.trigger_type = "form_submitted"
    workflow.trigger_types = ["form_submitted"]
    workflow.subscribed_event_keys = []
    workflow.definition = definition or _definition()
    return workflow


def _context(**overrides) -> dict:
    return {
        "event": "form.submitted",
        "trigger_type": "form_submitted",
        "contact_id": "contact-1",
        "location_id": "loc-1",
        "form_provider": "typeform",
        "form_id": "form-abc",
        "form_name": "ABC",
        "form_answers": {"problem": "Toothache"},
        **overrides,
    }


def _session(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    return session


# ── selection ───────────────────────────────────────────────────────────
def test_no_provider_and_no_forms_matches_anything() -> None:
    assert workflow_matches_submission(_workflow(), _context())


def test_a_named_provider_excludes_the_other() -> None:
    workflow = _workflow(
        definition=_definition({"type": "form_submitted", "provider": "meta"})
    )
    assert not workflow_matches_submission(workflow, _context(form_provider="typeform"))


def test_a_named_form_excludes_the_practices_other_forms() -> None:
    workflow = _workflow(
        definition=_definition(
            {"type": "form_submitted", "provider": "typeform", "form_ids": ["form-abc"]}
        )
    )
    assert workflow_matches_submission(workflow, _context(form_id="form-abc"))
    assert not workflow_matches_submission(workflow, _context(form_id="form-xyz"))


def test_a_trigger_filter_still_applies_after_the_form_matches() -> None:
    """"When ABC is submitted *and* Problem is X" — the whiteboard's if/else,
    moved in front of enrollment so an ineligible submission costs nothing."""
    workflow = _workflow(
        definition=_definition(
            {
                "type": "form_submitted",
                "form_ids": ["form-abc"],
                "filter": {
                    "kind": "rule",
                    "field": "form_answers.problem",
                    "op": "eq",
                    "value": "Toothache",
                },
            }
        )
    )
    assert workflow_matches_submission(workflow, _context())
    assert not workflow_matches_submission(
        workflow, _context(form_answers={"problem": "Cleaning"})
    )


def test_another_trigger_type_never_matches() -> None:
    workflow = _workflow(definition=_definition({"type": "enquiry_received"}))
    assert not workflow_matches_submission(workflow, _context())


def test_an_unparseable_definition_matches_nothing() -> None:
    workflow = _workflow(definition={"nonsense": True})
    assert not workflow_matches_submission(workflow, _context())


# ── dispatch ────────────────────────────────────────────────────────────
def test_dispatch_keys_on_the_submission_not_the_contact() -> None:
    """One submission enrolls once however many times it is redelivered; the
    same person submitting twice is two events and enrolls twice."""
    first = make_form_idempotency_key("ver-1", "submission-1")
    again = make_form_idempotency_key("ver-1", "submission-1")
    other = make_form_idempotency_key("ver-1", "submission-2")
    assert first == again
    assert first != other


def test_an_institution_wide_workflow_runs_at_the_forms_location() -> None:
    workflow = _workflow(location_id=None)
    session = _session([workflow])

    dispatches = asyncio.run(
        FormTriggerService(session).prepare_dispatches(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
            submission_id="submission-1",
            context=_context(),
        )
    )
    assert len(dispatches) == 1
    assert dispatches[0].location_id == "loc-1"
    kwargs = dispatches[0].task_kwargs()
    assert kwargs["trigger_type"] == "form_submitted"
    assert kwargs["trigger_ref_type"] == "form_submission"
    assert kwargs["trigger_ref_id"] == "submission-1"


def test_a_location_bound_workflow_ignores_another_locations_form() -> None:
    """Otherwise the run carries the event's location and the patient is
    contacted with the wrong clinic's number, hours and voice."""
    workflow = _workflow(location_id="loc-2")
    session = _session([workflow])

    dispatches = asyncio.run(
        FormTriggerService(session).prepare_dispatches(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
            submission_id="submission-1",
            context=_context(),
        )
    )
    assert dispatches == []


def test_a_workflow_with_no_published_version_is_skipped() -> None:
    workflow = _workflow()
    workflow.current_version_id = None
    session = _session([workflow])

    dispatches = asyncio.run(
        FormTriggerService(session).prepare_dispatches(
            institution_id="inst-1",
            location_id="loc-1",
            contact_id="contact-1",
            submission_id="submission-1",
            context=_context(),
        )
    )
    assert dispatches == []
