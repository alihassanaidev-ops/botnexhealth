"""The merge-field catalog must cover every trigger the schema accepts.

`appointment_state_changed` and `email_reply` were added to the definition schema
but never to `merge_field_catalog.WorkflowTriggerType`. Because `fields_for`
filters on membership, both resolved to an EMPTY catalog: the builder's
insert-field menu was blank on the primary GoTracker trigger, and
`validation_service` raised `merge_field_unavailable_for_trigger` for every token
in both live production campaigns.

These tests fail the moment a new trigger type is added to the schema without a
catalog scope, which is the only reliable guard against a silent repeat.
"""

from __future__ import annotations

import typing

import pytest

from src.app.services.automation.definition_schema import WorkflowTrigger
from src.app.services.automation.merge_field_catalog import (
    ALL_TRIGGERS,
    MERGE_FIELD_CATALOG,
    WorkflowTriggerType,
    fields_for,
)


def _schema_trigger_types() -> set[str]:
    """Every ``type`` literal in the discriminated ``WorkflowTrigger`` union."""
    # WorkflowTrigger is Annotated[Union[...], Field(discriminator="type")]
    union = typing.get_args(WorkflowTrigger)[0]
    return {
        typing.get_args(model.model_fields["type"].annotation)[0]
        for model in typing.get_args(union)
    }


def _catalog_trigger_types() -> set[str]:
    return set(typing.get_args(WorkflowTriggerType))


def test_catalog_declares_every_schema_trigger() -> None:
    missing = _schema_trigger_types() - _catalog_trigger_types()
    assert not missing, (
        f"Trigger type(s) {sorted(missing)} exist in definition_schema but not in "
        "merge_field_catalog.WorkflowTriggerType. Workflows using them resolve to "
        "zero merge fields and warn on every token."
    )


def test_catalog_declares_no_unknown_trigger() -> None:
    extra = _catalog_trigger_types() - _schema_trigger_types()
    assert not extra, f"Catalog scopes unknown trigger type(s) {sorted(extra)}."


def test_all_triggers_tuple_matches_the_literal() -> None:
    assert set(ALL_TRIGGERS) == _catalog_trigger_types()


@pytest.mark.parametrize("trigger_type", sorted(_schema_trigger_types()))
def test_every_trigger_resolves_fields(trigger_type: str) -> None:
    """Any trigger must at minimum offer the patient and location fields."""
    fields = fields_for(trigger_type=trigger_type)
    assert fields, f"{trigger_type} resolves an empty merge-field catalog"
    names = {field.name for field in fields}
    assert {"patient_first_name", "clinic_name"} <= names


def test_appointment_triggers_expose_appointment_fields() -> None:
    """The appointment-state trigger must reach the appointment context.

    Both live campaigns render appointment date/time; before this scope fix the
    post-op campaign could not insert them from the builder at all.
    """
    for trigger_type in ("appointment_offset", "appointment_state_changed"):
        names = {field.name for field in fields_for(trigger_type=trigger_type)}
        assert {
            "appointment_date",
            "appointment_time",
            "appointment_reason",
        } <= names, f"{trigger_type} is missing appointment merge fields"


def test_reply_fields_stay_on_their_own_channel() -> None:
    """SMS-named fields must not surface on the email-reply trigger."""
    email_names = {field.name for field in fields_for(trigger_type="email_reply")}
    assert "sms_reply_body" not in email_names
    assert "email_reply_intent" in email_names

    sms_names = {field.name for field in fields_for(trigger_type="sms_reply")}
    assert "email_reply_intent" not in sms_names
    assert "sms_reply_body" in sms_names


def test_enquiry_trigger_exposes_sales_enquiry_fields() -> None:
    names = {field.name for field in fields_for(trigger_type="enquiry_received")}

    assert {
        "enquiry_source",
        "enquiry_status",
        "matched_existing_contact",
        "booking_link",
        "registration_link",
    } <= names


def test_every_catalog_field_is_reachable_by_some_trigger() -> None:
    """A field scoped to no trigger can never be inserted."""
    for field in MERGE_FIELD_CATALOG:
        assert field.triggers, f"{field.name} is scoped to no trigger"
        unknown = set(field.triggers) - _catalog_trigger_types()
        assert not unknown, f"{field.name} references unknown trigger(s) {unknown}"
