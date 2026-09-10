"""Per-location value inputs: resolution, provenance, and cost apportionment."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app.api.routes.institution_portal import (
    LocationROIConfigRequest,
    _LOCATION_ROI_FIELDS,
    _billing_mode,
    _location_subscription_cost,
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
    # Not 22.0 from the institution. Absent rather than zero, so the staff
    # saving reports as unknown instead of as a saving of nothing.
    assert values["staff_hourly_rate"] is None


def test_subscription_cost_is_settable_per_location() -> None:
    """Both billing models are real deals, so the shape carries both."""
    assert "monthly_subscription_cost" in LocationROIConfigRequest.model_fields


def test_subscription_cost_is_never_inherited_from_the_institution() -> None:
    """The institution figure is the price of the whole group.

    Charging it to one clinic as though it were that clinic's own would
    overstate cost by the number of locations, so it is excluded from the
    fall-through set and read separately.
    """
    assert "monthly_subscription_cost" not in _LOCATION_ROI_FIELDS

    # A location inheriting the institution's operational numbers still reports
    # no subscription price of its own.
    assert _location_subscription_cost(_location(None)) is None
    values, source = _resolved_location_roi(
        _location(None), _institution(INSTITUTION_CONFIG)
    )
    assert source == "institution"
    assert "monthly_subscription_cost" not in values


def test_location_reports_its_own_price_when_set() -> None:
    location = _location({**LOCATION_CONFIG, "monthly_subscription_cost": 350.0})

    assert _location_subscription_cost(location) == 350.0


@pytest.mark.parametrize(
    "config, expected",
    [
        (None, "institution"),
        ({}, "institution"),
        ({"subscription_billing_mode": "location"}, "location"),
        ({"subscription_billing_mode": "institution"}, "institution"),
        # Anything unrecognised bills the way it always did rather than
        # inventing a third behaviour.
        ({"subscription_billing_mode": "per-seat"}, "institution"),
    ],
)
def test_billing_mode_is_read_from_the_institution(config, expected) -> None:
    assert _billing_mode(_institution(config)) == expected


def test_switching_mode_does_not_destroy_the_other_mode_s_price() -> None:
    """A location keeps its price while the tenant is billed per institution.

    Dropping it on switch would mean moving a tenant back and forth silently
    re-zeroes what each clinic is charged.
    """
    location = _location({**LOCATION_CONFIG, "monthly_subscription_cost": 350.0})
    institution = _institution(
        {**INSTITUTION_CONFIG, "subscription_billing_mode": "institution"}
    )

    assert _billing_mode(institution) == "institution"
    assert _location_subscription_cost(location) == 350.0


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


def test_missing_hourly_rate_is_unknown_not_zero() -> None:
    """A clinic that does not track a front desk rate has not saved nothing."""
    values, _ = _resolved_location_roi(
        _location({k: v for k, v in LOCATION_CONFIG.items() if k != "staff_hourly_rate"}),
        _institution(INSTITUTION_CONFIG),
    )

    assert values["staff_hourly_rate"] is None


def test_zero_hourly_rate_stays_zero() -> None:
    """Explicitly zero is a real answer and must not become "unknown"."""
    values, _ = _resolved_location_roi(
        _location({**LOCATION_CONFIG, "staff_hourly_rate": 0.0}),
        _institution(INSTITUTION_CONFIG),
    )

    assert values["staff_hourly_rate"] == 0.0


def test_hourly_rate_is_optional_on_the_request() -> None:
    request = LocationROIConfigRequest(
        avg_appointment_value=310.0,
        avg_new_patient_value=700.0,
    )

    assert request.staff_hourly_rate is None


# ---------------------------------------------------------------------------
# Institution totals summed from per-location figures.
#
# A group that priced each clinic has answered the question, just not in one
# place, so the institution view sums them instead of refusing.
# ---------------------------------------------------------------------------


def test_a_location_only_group_still_resolves_each_clinic() -> None:
    """Each clinic is valued with its own numbers, not an average of them."""
    downtown = _location({**LOCATION_CONFIG, "monthly_subscription_cost": 350.0})
    suburb = _location(
        {
            "avg_appointment_value": 150.0,
            "avg_new_patient_value": 300.0,
            "staff_hourly_rate": 18.0,
            "avg_call_duration_minutes": 4.0,
        }
    )
    # No institution config at all — the aggregate branch's precondition.
    uncofigured_institution = _institution(None)

    a, a_source = _resolved_location_roi(downtown, uncofigured_institution)
    b, b_source = _resolved_location_roi(suburb, uncofigured_institution)

    assert (a_source, b_source) == ("location", "location")
    assert a["avg_appointment_value"] == 310.0
    assert b["avg_appointment_value"] == 150.0
    # Averaging the two would value every booking at 230 and match neither.
    assert a["avg_appointment_value"] != b["avg_appointment_value"]


def test_a_location_with_no_figures_contributes_nothing() -> None:
    """It is skipped, not counted at zero, so it cannot dilute the total."""
    assert _resolved_location_roi(_location(None), _institution(None)) is None


def test_only_configured_clinics_carry_a_price_into_the_total() -> None:
    priced = _location({**LOCATION_CONFIG, "monthly_subscription_cost": 350.0})
    unpriced = _location(LOCATION_CONFIG)

    costs = [
        cost
        for cost in (
            _location_subscription_cost(priced),
            _location_subscription_cost(unpriced),
        )
        if cost is not None
    ]

    assert costs == [350.0]


# ---------------------------------------------------------------------------
# The route's own response, not just the arithmetic behind it.
#
# The helpers were unit-tested and correct while the endpoint 500'd for every
# caller: a required response field had been added to the model but wired into
# the wrong return statement, and nothing exercised the route itself. The
# dashboard swallowed the 500 as "not configured" and showed no cards.
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """Answers every count query with the same number."""

    def __init__(self, count: int = 4):
        self._count = count

    async def execute(self, *_args, **_kwargs):
        return _FakeResult(self._count)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


def test_calculate_location_roi_returns_a_valid_response(monkeypatch) -> None:
    import asyncio
    from datetime import date, timedelta

    from src.app.api.routes import institution_portal as portal

    location = _location({**LOCATION_CONFIG, "monthly_subscription_cost": None})
    location.name = "Downtown"
    institution = _institution(INSTITUTION_CONFIG)
    institution.pms_type = "nexhealth"

    class _Svc:
        def __init__(self, _session):
            pass

        async def get_location_by_slug(self, _slug, _institution_id):
            return location

        async def get_by_id(self, _institution_id):
            return institution

    monkeypatch.setattr(portal, "InstitutionService", _Svc)
    monkeypatch.setattr(portal, "get_db_session", lambda: _FakeSession(4))

    user = SimpleNamespace(
        id="u1", role="LOCATION_ADMIN", institution_id="inst-1", location_id="loc-1"
    )
    today = date.today()
    result = asyncio.run(
        portal.calculate_location_roi(
            loc_slug="downtown",
            current_user=user,
            start_date=today - timedelta(days=6),
            end_date=today,
        )
    )

    # The window has to come back, or the response model rejects it at runtime.
    assert result.period_start == today - timedelta(days=6)
    assert result.period_end == today
    # 4 bookings * 310 + 4 new patients * 700.
    assert result.total_revenue_generated == 4 * 310.0 + 4 * 700.0
    assert result.config.source == "location"
