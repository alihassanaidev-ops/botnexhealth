"""Per-location value inputs: resolution, provenance, and cost apportionment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.api.routes.institution_portal import (
    LocationROIConfigRequest,
    _LOCATION_ROI_FIELDS,
    _resolved_location_roi,
)

INSTITUTION_CONFIG = {
    "avg_appointment_value": 200.0,
    "avg_new_patient_value": 450.0,
    "monthly_subscription_cost": 900.0,
    "staff_hourly_rate": 22.0,
    "avg_call_duration_minutes": 4.0,
}

LOCATION_CONFIG = {
    "avg_appointment_value": 310.0,
    "avg_new_patient_value": 700.0,
    "staff_hourly_rate": 28.0,
    "avg_call_duration_minutes": 5.0,
}


def _location(config):
    return SimpleNamespace(id="loc-1", slug="downtown", roi_config=config)


def _institution(config):
    return SimpleNamespace(id="inst-1", roi_config=config)


def test_location_numbers_win_and_are_reported_as_the_location_s_own() -> None:
    values, source = _resolved_location_roi(
        _location(LOCATION_CONFIG), _institution(INSTITUTION_CONFIG)
    )

    assert source == "location"
    assert values["avg_appointment_value"] == 310.0
    assert values["staff_hourly_rate"] == 28.0


def test_institution_numbers_stand_in_but_say_so() -> None:
    """The clinic must be able to tell a group average from its own figure.

    Reporting an inherited number as though the location had set it is how a
    practice ends up quoting a group-wide average back as its own performance.
    """
    values, source = _resolved_location_roi(
        _location(None), _institution(INSTITUTION_CONFIG)
    )

    assert source == "institution"
    assert values["avg_appointment_value"] == 200.0


@pytest.mark.parametrize("empty", [None, {}])
def test_unset_everywhere_resolves_to_nothing(empty) -> None:
    assert _resolved_location_roi(_location(empty), _institution(empty)) is None


def test_fall_through_is_whole_config_not_field_by_field() -> None:
    """A location that sets any of its own numbers uses only its own.

    Blending one location field with three institution ones yields a figure that
    is neither, and nothing in the response could tell a reader which was which.
    """
    partial = {"avg_appointment_value": 310.0}
    values, source = _resolved_location_roi(
        _location(partial), _institution(INSTITUTION_CONFIG)
    )

    assert source == "location"
    assert values["avg_appointment_value"] == 310.0
    # Not 22.0 from the institution.
    assert values["staff_hourly_rate"] == 0.0


def test_subscription_cost_is_not_a_per_location_input() -> None:
    """It is billed once per institution.

    Accepting it here would let a two-location group subtract the same 900 twice
    and report a worse ROI than it has.
    """
    assert "monthly_subscription_cost" not in _LOCATION_ROI_FIELDS
    assert "monthly_subscription_cost" not in LocationROIConfigRequest.model_fields


def test_request_rejects_negative_money() -> None:
    with pytest.raises(ValueError):
        LocationROIConfigRequest(
            avg_appointment_value=-1.0,
            avg_new_patient_value=450.0,
            staff_hourly_rate=22.0,
        )


def test_call_duration_has_a_default_so_it_is_optional() -> None:
    request = LocationROIConfigRequest(
        avg_appointment_value=310.0,
        avg_new_patient_value=700.0,
        staff_hourly_rate=28.0,
    )

    assert request.avg_call_duration_minutes == 4.0
