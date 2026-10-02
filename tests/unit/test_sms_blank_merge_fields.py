"""A send that renders a merge field blank must say so.

The SMS renderer substitutes a missing value with an empty string, deliberately:
a patient must never receive raw ``{{token}}`` text. The cost is that a campaign
can go out with a hole in it — "see you on ." — and nothing anywhere records
that it happened. These tests cover the reporting side of that trade.
"""

from __future__ import annotations

from src.app.services.automation.template_renderer import (
    render_sms_body,
    render_sms_body_reporting_blanks,
)


def test_reports_the_field_that_rendered_blank() -> None:
    body, blanks = render_sms_body_reporting_blanks(
        "Hi {{patient_first_name}}, see you on {{appointment_date}}.",
        None,
        None,
        {"patient_first_name": "Sarah"},
    )

    assert body == "Hi Sarah, see you on ."
    assert blanks == ["appointment_date"]


def test_reports_nothing_when_every_field_resolves() -> None:
    body, blanks = render_sms_body_reporting_blanks(
        "Hi {{patient_first_name}}.",
        None,
        None,
        {"patient_first_name": "Sarah"},
    )

    assert body == "Hi Sarah."
    assert blanks == []


def test_whitespace_only_counts_as_blank() -> None:
    """A value of spaces reads as a gap to the patient just the same."""
    _, blanks = render_sms_body_reporting_blanks(
        "See you on {{appointment_date}}.", None, None, {"appointment_date": "   "}
    )

    assert blanks == ["appointment_date"]


def test_a_field_used_twice_is_reported_once() -> None:
    _, blanks = render_sms_body_reporting_blanks(
        "{{appointment_date}} — {{appointment_date}}", None, None, {}
    )

    assert blanks == ["appointment_date"]


def test_render_sms_body_is_unchanged_by_the_reporting_split() -> None:
    """The send path's output must be byte-identical to before the refactor."""
    template = "Hi {{patient_first_name}}, on {{appointment_date}} at {{unknown_token}}."
    context = {"patient_first_name": "Sarah"}

    body, _ = render_sms_body_reporting_blanks(template, None, None, context)

    assert render_sms_body(template, None, None, context) == body
    assert render_sms_body(template, None, None, context) == "Hi Sarah, on  at ."
