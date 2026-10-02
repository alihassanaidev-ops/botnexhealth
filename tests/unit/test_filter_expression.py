"""The shared filter DSL used by triggers, conditions, switch cases and audiences."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import TypeAdapter, ValidationError

from src.app.services.automation.filter_expression import (
    EvaluationContext,
    FilterExpression,
    evaluate,
    referenced_fields,
)

_ADAPTER = TypeAdapter(FilterExpression)
_NOW = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)


def parse(payload: dict) -> FilterExpression:
    return _ADAPTER.validate_python(payload)


def rule(**kwargs) -> FilterExpression:
    return parse({"kind": "rule", **kwargs})


def group(op: str, *children: dict) -> FilterExpression:
    return parse({"kind": "group", "op": op, "children": list(children)})


def ctx(values: dict, *, tz: str = "UTC") -> EvaluationContext:
    return EvaluationContext(values=values, now=_NOW, timezone_name=tz)


# ---------------------------------------------------------------------------
# Equality and coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("actual", "expected", "result"),
    [
        ("booked", "booked", True),
        ("Booked", "booked", True),  # case-insensitive by default
        (" booked ", "booked", True),  # whitespace-insensitive
        ("booked", "cancelled", False),
        # Webhook payloads deliver numbers as strings; an author writes a number.
        ("1", 1, True),
        (1, "1", True),
        ("1.0", 1, True),
        ("true", True, True),
        ("no", False, True),
        (None, "x", False),
    ],
)
def test_eq_coerces_across_wire_types(actual, expected, result) -> None:
    assert evaluate(rule(field="v", op="eq", value=expected), ctx({"v": actual})) is result


def test_eq_can_be_made_case_sensitive() -> None:
    strict = rule(field="v", op="eq", value="Booked", case_sensitive=True)
    assert evaluate(strict, ctx({"v": "Booked"})) is True
    assert evaluate(strict, ctx({"v": "booked"})) is False


def test_neq_is_the_negation_of_eq() -> None:
    assert evaluate(rule(field="v", op="neq", value="booked"), ctx({"v": "cancelled"}))
    assert not evaluate(rule(field="v", op="neq", value="booked"), ctx({"v": "BOOKED"}))


# ---------------------------------------------------------------------------
# Missing data
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "op,value",
    [
        ("eq", "x"),
        ("gt", 1),
        ("contains", "x"),
        ("starts_with", "x"),
        ("before", "now"),
        ("within", "P1D"),
        ("matches", "x"),
        ("any_of", ["x"]),
    ],
)
def test_missing_field_never_matches(op, value) -> None:
    """"The data never arrived" must not read as "the value did not match"."""
    assert evaluate(rule(field="absent", op=op, value=value), ctx({})) is False


def test_presence_operators_are_the_only_way_to_test_absence() -> None:
    empty = ctx({"blank": "   ", "none": None, "list": [], "set": "x"})
    assert evaluate(rule(field="none", op="is_null"), empty)
    assert evaluate(rule(field="set", op="is_not_null"), empty)
    assert evaluate(rule(field="blank", op="is_empty"), empty)
    assert evaluate(rule(field="list", op="is_empty"), empty)
    assert evaluate(rule(field="set", op="is_not_empty"), empty)
    # A field that is absent entirely is null and empty.
    assert evaluate(rule(field="absent", op="is_null"), empty)
    assert evaluate(rule(field="absent", op="is_empty"), empty)


# ---------------------------------------------------------------------------
# Membership — the published spelling must keep working
# ---------------------------------------------------------------------------


def test_in_is_case_sensitive_and_in_case_insensitive_is_not() -> None:
    context = ctx({"v": "Bridge Prep"})
    assert not evaluate(rule(field="v", op="in", value=["bridge prep"]), context)
    assert evaluate(
        rule(field="v", op="in_case_insensitive", value=["bridge prep"]), context
    )
    assert not evaluate(
        rule(field="v", op="not_in_case_insensitive", value=["bridge prep"]), context
    )


# ---------------------------------------------------------------------------
# Numeric and text operators the old condition node could not express
# ---------------------------------------------------------------------------


def test_numeric_comparison() -> None:
    context = ctx({"amount": "2400"})
    assert evaluate(rule(field="amount", op="gt", value=2000), context)
    assert evaluate(rule(field="amount", op="gte", value=2400), context)
    assert not evaluate(rule(field="amount", op="lt", value=2000), context)
    assert evaluate(rule(field="amount", op="between", value=[1000, 3000]), context)
    assert not evaluate(rule(field="amount", op="between", value=[3000, 4000]), context)


def test_text_operators() -> None:
    context = ctx({"reason": "Implant Surgery Stage 2"})
    assert evaluate(rule(field="reason", op="contains", value="implant"), context)
    assert evaluate(rule(field="reason", op="starts_with", value="implant"), context)
    assert evaluate(rule(field="reason", op="ends_with", value="stage 2"), context)
    assert evaluate(rule(field="reason", op="matches", value=r"stage \d"), context)
    assert not evaluate(rule(field="reason", op="not_contains", value="implant"), context)


def test_contains_looks_inside_lists() -> None:
    context = ctx({"reasons": ["bridge prep", "cleaning"]})
    assert evaluate(rule(field="reasons", op="contains", value="cleaning"), context)
    assert not evaluate(rule(field="reasons", op="contains", value="extraction"), context)


def test_any_of_and_all_of_over_an_array_field() -> None:
    context = ctx({"reasons": ["bridge prep", "cleaning"]})
    assert evaluate(rule(field="reasons", op="any_of", value=["cleaning", "x"]), context)
    assert not evaluate(rule(field="reasons", op="all_of", value=["cleaning", "x"]), context)
    assert evaluate(
        rule(field="reasons", op="all_of", value=["cleaning", "bridge prep"]), context
    )


# ---------------------------------------------------------------------------
# Temporal
# ---------------------------------------------------------------------------


def test_before_and_after_understand_relative_now() -> None:
    context = ctx({"start": "2026-08-31T09:00:00+00:00"})
    assert evaluate(rule(field="start", op="after", value="now"), context)
    assert evaluate(rule(field="start", op="before", value="now+P7D"), context)
    assert not evaluate(rule(field="start", op="before", value="now+P1D"), context)
    assert evaluate(rule(field="start", op="after", value="now-P1D"), context)


def test_within_and_older_than_measure_distance_from_now() -> None:
    context = ctx({"seen": "2026-08-28T12:00:00+00:00"})
    assert evaluate(rule(field="seen", op="within", value="P2D"), context)
    assert not evaluate(rule(field="seen", op="within", value="PT6H"), context)
    assert evaluate(rule(field="seen", op="older_than", value="PT6H"), context)


def test_date_only_values_resolve_in_the_location_timezone() -> None:
    """"Before today" must mean today at the clinic, not today in UTC."""
    # 03:00 UTC on the 29th is still the 28th in Toronto (UTC-4).
    context = ctx({"at": "2026-08-29T03:00:00+00:00"}, tz="America/Toronto")
    assert evaluate(rule(field="at", op="before", value="2026-08-29"), context)
    utc_context = ctx({"at": "2026-08-29T03:00:00+00:00"}, tz="UTC")
    assert not evaluate(rule(field="at", op="before", value="2026-08-29"), utc_context)


def test_naive_datetimes_are_read_as_clinic_local() -> None:
    """GoTracker sends wall-clock timestamps with no offset."""
    toronto = ctx({"at": "2026-08-29T09:00:00"}, tz="America/Toronto")
    # 09:00 Toronto is 13:00 UTC, which is after the 12:00 UTC "now".
    assert evaluate(rule(field="at", op="after", value="now"), toronto)
    utc = ctx({"at": "2026-08-29T09:00:00"}, tz="UTC")
    assert evaluate(rule(field="at", op="before", value="now"), utc)


# ---------------------------------------------------------------------------
# Field-to-field
# ---------------------------------------------------------------------------


def test_field_to_field_comparison() -> None:
    context = ctx({"original": "2026-08-01T00:00:00Z", "current": "2026-08-05T00:00:00Z"})
    assert evaluate(rule(field="current", op="field_gt", value="original"), context)
    assert evaluate(rule(field="original", op="field_lt", value="current"), context)
    assert evaluate(rule(field="current", op="field_neq", value="original"), context)
    assert not evaluate(rule(field="current", op="field_eq", value="original"), context)


# ---------------------------------------------------------------------------
# Nesting — the thing the flat condition node could not do at all
# ---------------------------------------------------------------------------


def test_nested_and_or_groups() -> None:
    expression = group(
        "and",
        {"kind": "rule", "field": "type", "op": "eq", "value": "surgery"},
        {
            "kind": "group",
            "op": "or",
            "children": [
                {"kind": "rule", "field": "start", "op": "before", "value": "now+P14D"},
                {"kind": "rule", "field": "vip", "op": "eq", "value": True},
            ],
        },
    )
    assert evaluate(expression, ctx({"type": "surgery", "start": "2026-09-01T00:00:00Z"}))
    assert evaluate(expression, ctx({"type": "surgery", "start": "2027-01-01T00:00:00Z", "vip": True}))
    assert not evaluate(expression, ctx({"type": "surgery", "start": "2027-01-01T00:00:00Z"}))
    assert not evaluate(expression, ctx({"type": "cleaning", "start": "2026-09-01T00:00:00Z"}))


def test_not_group_negates_the_conjunction() -> None:
    expression = group(
        "not", {"kind": "rule", "field": "status", "op": "eq", "value": "cancelled"}
    )
    assert evaluate(expression, ctx({"status": "booked"}))
    assert not evaluate(expression, ctx({"status": "cancelled"}))


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def test_dotted_and_indexed_paths_resolve() -> None:
    context = ctx(
        {"appointment": {"provider": {"name": "Dr Chan"}, "reasons": ["a", "b"]}}
    )
    assert evaluate(
        rule(field="appointment.provider.name", op="eq", value="dr chan"), context
    )
    assert evaluate(rule(field="appointment.reasons[1]", op="eq", value="b"), context)


def test_flat_key_wins_over_a_dotted_walk() -> None:
    """Run context carries both shapes; an exact key must not be shadowed."""
    context = ctx({"a.b": "flat", "a": {"b": "nested"}})
    assert evaluate(rule(field="a.b", op="eq", value="flat"), context)


def test_referenced_fields_collects_both_sides() -> None:
    expression = group(
        "and",
        {"kind": "rule", "field": "x", "op": "is_null"},
        {"kind": "rule", "field": "a", "op": "field_eq", "value": "b"},
    )
    assert referenced_fields(expression) == {"x", "a", "b"}


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "rule", "field": "x", "op": "is_null", "value": "nope"},
        {"kind": "rule", "field": "x", "op": "eq"},
        {"kind": "rule", "field": "x", "op": "in", "value": "not-a-list"},
        {"kind": "rule", "field": "x", "op": "in", "value": []},
        {"kind": "rule", "field": "x", "op": "between", "value": [1, 2, 3]},
        {"kind": "rule", "field": "x", "op": "eq", "value": ["a"]},
        {"kind": "rule", "field": "x", "op": "within", "value": "2 weeks"},
        {"kind": "rule", "field": "x", "op": "within", "value": "P1M"},
        {"kind": "rule", "field": "x", "op": "matches", "value": "(unclosed"},
        {"kind": "rule", "field": "x", "op": "field_eq", "value": 3},
        {"kind": "rule", "field": " ", "op": "is_null"},
        {"kind": "rule", "field": "x", "op": "no_such_op", "value": 1},
        {"kind": "group", "op": "and", "children": []},
        {"kind": "group", "op": "xor", "children": [{"kind": "rule", "field": "x", "op": "is_null"}]},
    ],
)
def test_invalid_expressions_are_rejected(payload) -> None:
    with pytest.raises(ValidationError):
        parse(payload)


def test_months_are_rejected_because_they_are_not_fixed_length() -> None:
    """P1M would mean different things in February and March."""
    with pytest.raises(ValidationError):
        parse({"kind": "rule", "field": "x", "op": "within", "value": "P1M"})
    parse({"kind": "rule", "field": "x", "op": "within", "value": "P30D"})


def test_oversized_regex_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse({"kind": "rule", "field": "x", "op": "matches", "value": "a" * 500})


def test_bad_data_returns_false_rather_than_raising() -> None:
    """A filter that cannot be evaluated must not abort a run."""
    context = ctx({"v": {"unexpected": "dict"}})
    assert evaluate(rule(field="v", op="gt", value=1), context) is False
    assert evaluate(rule(field="v", op="contains", value="x"), context) is False
    assert evaluate(rule(field="v", op="before", value="now"), context) is False
