"""Regression coverage for Retell PII placeholders entering workflow data."""

from types import SimpleNamespace

from src.app.services.automation.merge_field_catalog import MergeContextBuilder
from src.app.services.post_call_service import PostCallService, _nonempty


def test_post_call_identity_ignores_retell_person_name_placeholder() -> None:
    assert _nonempty("[person name 1]") is None
    assert PostCallService._extract_name(
        {},
        {
            "first_name": "[person name 1]",
            "last_name": "[person name 2]",
        },
    ) == (None, None, None)


def test_voice_merge_context_falls_back_when_contact_name_is_scrubbed() -> None:
    contact = SimpleNamespace(
        first_name="[person name 1]",
        last_name=None,
        full_name="[person name 1]",
    )

    merged = MergeContextBuilder.build(
        contact=contact,
        context={
            "patient_first_name": "Hammad",
            "patient_full_name": "Hammad",
        },
    )

    assert merged["patient_first_name"] == "Hammad"
    assert merged["patient_full_name"] == "Hammad"
