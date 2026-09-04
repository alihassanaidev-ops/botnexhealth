"""The merge-field catalog must cover every trigger the schema accepts.

Two of the pre-rearchitecture trigger types were added to the definition schema
but never to `merge_field_catalog.WorkflowTriggerType`. Because `fields_for`
filters on membership, both resolved to an EMPTY catalog: the builder's
insert-field menu was blank on the primary appointment trigger, and
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
    APPOINTMENT_TRIGGERS,
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
    """A trigger whose run carries an appointment must reach the appointment context.

    Both live campaigns render appointment date/time; before this scope fix the
    post-op campaign could not insert them from the builder at all. The two
    appointment trigger types are now one ``event`` trigger, plus
    ``internal_status``, whose run inherits the appointment from the run that
    recorded the status.
    """
    for trigger_type in APPOINTMENT_TRIGGERS:
        names = {field.name for field in fields_for(trigger_type=trigger_type)}
        assert {
            "appointment_date",
            "appointment_time",
            "appointment_reason",
        } <= names, f"{trigger_type} is missing appointment merge fields"


def test_reply_fields_reach_only_the_inbound_message_trigger() -> None:
    """Both reply field families belong to ``inbound_message`` and nothing else.

    ``sms_reply`` and ``email_reply`` were merged into one trigger, so the
    separation that used to be per-trigger is now per-group: an author picking
    the SMS channel still sees the SMS group, but the catalog scope is shared.
    What must not regress is the field families leaking onto unrelated triggers.
    """
    inbound_fields = fields_for(trigger_type="inbound_message")
    inbound_names = {field.name for field in inbound_fields}
    assert {"sms_reply_body", "sms_reply_intent", "email_reply_intent"} <= inbound_names

    groups = {
        field.name: field.group for field in inbound_fields if field.name in inbound_names
    }
    assert groups["sms_reply_body"] == "sms_reply"
    assert groups["email_reply_intent"] == "email_reply"

    for trigger_type in ALL_TRIGGERS:
        if trigger_type == "inbound_message":
            continue
        other = {field.name for field in fields_for(trigger_type=trigger_type)}
        assert not (
            {"sms_reply_body", "sms_reply_intent", "email_reply_intent"} & other
        ), f"{trigger_type} exposes reply fields its runs never carry"


def test_reply_body_is_never_insertable_into_an_sms() -> None:
    """Quoting the patient's own text back at them over SMS is not offered."""
    sms_names = {
        field.name for field in fields_for(trigger_type="inbound_message", channel="sms")
    }
    assert "sms_reply_body" not in sms_names
    assert "sms_reply_intent" in sms_names


def test_enquiry_trigger_exposes_sales_enquiry_fields() -> None:
    # `enquiry_received` is now the `enquiry.received` key on the event trigger.
    names = {field.name for field in fields_for(trigger_type="event")}

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
