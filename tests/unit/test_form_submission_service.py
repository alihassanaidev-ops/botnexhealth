"""Landing one submitted response.

The behaviours here are the ones that go wrong quietly: a redelivered webhook
creating a second contact, a form's full-name answer overwriting the first name
it also asked for, consent being inferred from the act of submitting, and
identifying answers leaking into a workflow's run context.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.app.services.forms.mapping_service import MappedSubmission
from src.app.services.forms.providers.base import NormalizedSubmission
from src.app.services.forms.submission_service import (
    LandedSubmission,
    SubmissionRejected,
    _consent_channels,
    _resolve_name,
    land_submission,
    record_unprocessed_submission,
    submission_trigger_context,
)

MODULE = "src.app.services.forms.submission_service"


def _form(**overrides):
    defaults = {
        "id": "form-1",
        "institution_id": "inst-1",
        "location_id": "loc-1",
        "provider": "typeform",
        "external_form_id": "AbC123",
        "name": "New Patient Enquiry",
        "source_name": "typeform_abc",
        "consent_sms": False,
        "consent_email": False,
        "consent_wording": None,
        "last_submission_at": None,
    }
    return SimpleNamespace(**{**defaults, **overrides})


def _session(existing=None):
    """A session whose first lookup answers with ``existing``."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.add = MagicMock()
    return session


def _mapped(**overrides) -> MappedSubmission:
    return MappedSubmission(
        contact_fields=overrides.get(
            "contact_fields", {"email": "mary@example.com", "first_name": "Mary"}
        ),
        custom_field_values=overrides.get("custom_field_values", []),
        context_answers=overrides.get("context_answers", {"problem": "Toothache"}),
        unmapped_keys=overrides.get("unmapped_keys", []),
    )


def _land(session, form, submission, mapped):
    with (
        patch(f"{MODULE}.load_mappings", AsyncMock(return_value=[])),
        patch(f"{MODULE}.load_custom_field_definitions", AsyncMock(return_value={})),
        patch(f"{MODULE}.apply_mapping", return_value=mapped),
        patch(
            f"{MODULE}.intake_enquiry",
            AsyncMock(
                return_value=SimpleNamespace(
                    enquiry=SimpleNamespace(id="contact-1", nexhealth_patient_id=None),
                    created=True,
                    matched_existing_contact=False,
                )
            ),
        ) as intake,
        patch(f"{MODULE}._write_custom_field_values", AsyncMock()),
    ):
        landed = asyncio.run(
            land_submission(session, form=form, submission=submission, raw_body=None)
        )
    return landed, intake


def test_a_redelivered_submission_lands_once() -> None:
    """Both providers retry. A second contact for one response is the failure."""
    session = _session(existing=SimpleNamespace(id="already-here"))
    landed, _ = _land(
        session,
        _form(),
        NormalizedSubmission(external_submission_id="response-1", answers={}),
        _mapped(),
    )
    assert landed is None


def test_a_lead_with_no_way_to_reach_them_is_refused() -> None:
    with pytest.raises(SubmissionRejected) as raised:
        _land(
            _session(),
            _form(),
            NormalizedSubmission(external_submission_id="response-1", answers={}),
            _mapped(contact_fields={"first_name": "Mary"}),
        )
    # Carried so the caller can record *which* submission was refused; without
    # it the drop is a log line and the practice never learns a lead was lost.
    assert raised.value.external_submission_id == "response-1"


# ── recording what did not become a contact ─────────────────────────────
def test_an_unprocessed_submission_is_written_down_with_its_reason() -> None:
    session = _session()
    row = asyncio.run(
        record_unprocessed_submission(
            session,
            form=_form(),
            external_submission_id="response-1",
            status="dropped",
            reason="A response arrived while this form was switched off.",
        )
    )
    assert row is not None
    assert row.status == "dropped"
    assert row.contact_id is None
    # No mapped answers: the mapping is either what failed or was never finished.
    assert row.context_answers is None
    assert "switched off" in (row.error_summary or "")
    session.add.assert_called_once()


def test_the_same_drop_is_recorded_once() -> None:
    """Providers redeliver; a count that inflates on retry is worse than none."""
    session = _session(existing=SimpleNamespace(id="already-here"))
    row = asyncio.run(
        record_unprocessed_submission(
            session,
            form=_form(),
            external_submission_id="response-1",
            status="dropped",
            reason="switched off",
        )
    )
    assert row is None
    session.add.assert_not_called()


def test_a_drop_with_no_provider_id_is_not_recorded() -> None:
    """Nothing to key on means a row per redelivery, which is a worse problem
    than the one this solves."""
    session = _session()
    row = asyncio.run(
        record_unprocessed_submission(
            session,
            form=_form(),
            external_submission_id=None,
            status="dropped",
            reason="switched off",
        )
    )
    assert row is None
    session.add.assert_not_called()


def test_the_intake_key_is_scoped_to_the_form_and_response() -> None:
    """So one person filling two different forms is matched on identifier
    rather than colliding on a shared key."""
    session = _session()
    _, intake = _land(
        session,
        _form(),
        NormalizedSubmission(external_submission_id="response-1", answers={}),
        _mapped(),
    )
    assert intake.await_args.kwargs["intake_key"] == "form:form-1:response-1"
    assert intake.await_args.kwargs["source"] == "typeform_abc"


def test_a_provider_that_sends_no_response_id_still_gets_idempotency() -> None:
    session = _session()
    _, intake = _land(
        session,
        _form(),
        NormalizedSubmission(
            external_submission_id="", answers={"email": "mary@example.com"}
        ),
        _mapped(),
    )
    key = intake.await_args.kwargs["intake_key"]
    assert key.startswith("form:form-1:")
    assert key != "form:form-1:"


def test_consent_is_taken_only_from_what_the_form_declares() -> None:
    assert _consent_channels(_form()) == ()
    assert _consent_channels(_form(consent_sms=True)) == ("sms",)
    assert _consent_channels(_form(consent_sms=True, consent_email=True)) == (
        "sms",
        "email",
    )


def test_consent_wording_travels_with_the_consent() -> None:
    session = _session()
    _, intake = _land(
        session,
        _form(consent_sms=True, consent_wording="I agree to be texted."),
        NormalizedSubmission(external_submission_id="response-1", answers={}),
        _mapped(),
    )
    assert intake.await_args.kwargs["consent_channels"] == ("sms",)
    assert intake.await_args.kwargs["consent_wording"] == "I agree to be texted."


# ── name resolution ─────────────────────────────────────────────────────
def test_separate_name_questions_win_over_a_full_name_answer() -> None:
    """A form asking both is authoritative; splitting the full name on top of
    it would turn "Mary Anne" into "Mary"."""
    assert _resolve_name(
        {"first_name": "Mary Anne", "last_name": "Smith", "full_name": "Mary A Smith"}
    ) == ("Mary Anne", "Smith")


def test_a_full_name_is_split_only_when_nothing_else_asked() -> None:
    assert _resolve_name({"full_name": "Mary Anne Smith"}) == ("Mary", "Anne Smith")
    assert _resolve_name({"full_name": "Prince"}) == ("Prince", None)
    assert _resolve_name({}) == (None, None)


# ── trigger context ─────────────────────────────────────────────────────
def _landed(mapped: MappedSubmission) -> LandedSubmission:
    return LandedSubmission(
        submission=SimpleNamespace(id="sub-1", external_submission_id="response-1"),
        contact=SimpleNamespace(id="contact-1", nexhealth_patient_id=None),
        mapped=mapped,
        created=True,
        matched_existing_contact=False,
    )


def test_the_run_context_carries_the_answers_a_workflow_branches_on() -> None:
    context = submission_trigger_context(
        form=_form(), landed=_landed(_mapped())
    )
    assert context["form_answers"] == {"problem": "Toothache"}
    assert context["form"]["answers"] == {"problem": "Toothache"}
    assert context["form_provider"] == "typeform"
    assert context["form_id"] == "form-1"
    assert context["trigger_type"] == "form_submitted"


def test_the_run_context_never_carries_identity() -> None:
    """Names, emails and phone numbers stay on the contact, read back through
    merge fields — which is the path access control already covers."""
    mapped = _mapped(
        contact_fields={"email": "mary@example.com", "phone": "+441234567890"},
        context_answers={"problem": "Toothache"},
    )
    context = submission_trigger_context(form=_form(), landed=_landed(mapped))
    serialized = str(context)
    assert "mary@example.com" not in serialized
    assert "+441234567890" not in serialized
