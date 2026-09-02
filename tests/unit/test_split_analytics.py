"""Per-variant (A/B) analytics: leader, lift, and the volume guard."""

from __future__ import annotations

from src.app.services.automation.campaign_analytics_service import (
    MIN_ARM_ENROLLMENTS,
    _authored_split_nodes,
    _split_node_analytics,
)

_AUTHORED = {
    "type": "split",
    "id": "ab",
    "subject": "Reminder wording",
    "branches": [
        {"label": "Variant A", "weight": 50, "next_node_id": "a"},
        {"label": "Variant B", "weight": 50, "next_node_id": "b"},
    ],
}


def _measured(label: str, enrollments: int, booked: int) -> tuple[str, dict]:
    return label, {"enrollments": enrollments, "booked": booked, "total_cost": 0}


def _analytics(*measured, authored=_AUTHORED):
    return _split_node_analytics(
        node_id="ab",
        category="recall",
        authored=authored,
        measured=list(measured),
    )


def test_the_better_arm_leads_and_reports_its_lift() -> None:
    result = _analytics(
        _measured("Variant A", 1000, 100),  # 10%
        _measured("Variant B", 1000, 120),  # 12%
    )
    assert result.primary_outcome_key == "booked"
    assert result.has_enough_volume is True

    a, b = result.branches
    assert (a.label, b.label) == ("Variant A", "Variant B")
    assert b.is_leader is True
    assert a.is_leader is False
    # +20% relative, not +2 points: the arms are compared on their own rates.
    assert b.lift is not None and round(b.lift, 3) == 0.2
    assert a.lift is not None and round(a.lift, 4) == round(-1 / 6, 4)


def test_no_winner_is_named_before_there_is_enough_volume() -> None:
    """A lead on a handful of contacts is noise, and calling it ends tests early."""
    under = MIN_ARM_ENROLLMENTS - 1
    result = _analytics(
        _measured("Variant A", under, 1),
        _measured("Variant B", under, 5),
    )
    assert result.has_enough_volume is False
    assert [b.is_leader for b in result.branches] == [False, False]
    assert [b.lift for b in result.branches] == [None, None]
    # The rates still show — withholding them would read as a broken panel.
    assert all(b.primary_rate is not None for b in result.branches)


def test_one_thin_arm_holds_back_the_whole_verdict() -> None:
    result = _analytics(
        _measured("Variant A", MIN_ARM_ENROLLMENTS * 5, 500),
        _measured("Variant B", MIN_ARM_ENROLLMENTS - 1, 40),
    )
    assert result.has_enough_volume is False
    assert all(not b.is_leader for b in result.branches)


def test_an_arm_that_has_routed_nobody_still_appears() -> None:
    """At zero, not absent: "nobody got here yet" is not "the split is off"."""
    result = _analytics(_measured("Variant A", 10, 1))
    assert [b.label for b in result.branches] == ["Variant A", "Variant B"]
    assert result.branches[1].enrollments == 0
    assert result.branches[1].primary_rate is None


def test_a_published_split_with_no_traffic_reports_both_arms_at_zero() -> None:
    result = _analytics()
    assert [b.enrollments for b in result.branches] == [0, 0]
    assert result.has_enough_volume is False


def test_arms_are_ordered_as_authored_not_alphabetically() -> None:
    result = _analytics(
        _measured("Variant B", 200, 20),
        _measured("Variant A", 200, 30),
    )
    assert [b.label for b in result.branches] == ["Variant A", "Variant B"]


def test_a_renamed_arm_keeps_its_history_and_is_marked_as_gone() -> None:
    """Editing a split must not erase what the old arms actually produced."""
    result = _analytics(
        _measured("Variant A", 200, 20),
        _measured("Old wording", 200, 10),
    )
    labels = [b.label for b in result.branches]
    assert labels == ["Variant A", "Variant B", "Old wording"]
    gone = result.branches[2]
    assert gone.weight is None
    assert gone.enrollments == 200


def test_authored_split_nodes_ignores_other_node_types() -> None:
    definition = {
        "nodes": [
            {"type": "send_sms", "id": "sms"},
            _AUTHORED,
            {"type": "switch", "id": "route"},
        ]
    }
    assert list(_authored_split_nodes(definition)) == ["ab"]
    assert _authored_split_nodes(None) == {}
