"""A field an author picks in one panel must work in the next.

The complaint this answers: fields looked random because discovery and
injection used different vocabularies. The Condition step offered
`appointment.status`, the message editor offered `{{appointment_datetime}}`,
and a dotted token typed into a message reached the patient as literal braces.
"""

from __future__ import annotations

from src.app.services.automation.event_catalog import fields_for_events
from src.app.services.automation.template_renderer import (
    extract_tokens,
    render_sms_body_reporting_blanks,
)

CONTEXT = {
    "appointment": {"status": "cancelled", "reason": "implant surgery"},
    "patient": {"first_name": "Jordan"},
    "patient_first_name": "Jordan",
    "appointment_date": "September 4, 2026",
}


def _render(template: str) -> tuple[str, list[str]]:
    return render_sms_body_reporting_blanks(template, None, None, CONTEXT)


def test_a_canonical_token_renders_rather_than_reaching_the_patient() -> None:
    """`{{appointment.status}}` used to be delivered braces and all."""
    body, blanks = _render("Your appointment is {{appointment.status}}")

    assert body == "Your appointment is cancelled"
    assert "{{" not in body
    assert blanks == []


def test_a_missing_value_is_still_reported_for_the_run_record() -> None:
    """Staging's behaviour, kept: send anyway, but record the hole."""
    body, blanks = _render("Time for your {{recall.type}} visit")

    assert body == "Time for your  visit"
    assert blanks == ["recall.type"]


def test_a_fallback_fills_the_hole_and_clears_the_blank() -> None:
    body, blanks = _render('Time for your {{recall.type | "check-up"}} visit')

    assert body == "Time for your check-up visit"
    assert blanks == []


def test_a_derived_merge_field_still_wins_over_a_context_path() -> None:
    """A published `{{appointment_date}}` must keep its formatted value."""
    body, _ = _render("On {{appointment_date}}")
    assert body == "On September 4, 2026"


def test_dotted_and_flat_tokens_are_both_extracted() -> None:
    """Publish validation and the renderer must see the same tokens."""
    assert extract_tokens('{{a}} {{b.c.d}} {{e | "x"}}') == ["a", "b.c.d", "e"]


def test_every_offered_field_is_a_token_the_renderer_understands() -> None:
    """The insert menu must not offer something the renderer cannot substitute.

    This is the check that keeps discovery and injection on one vocabulary.
    """
    for spec in fields_for_events(["appointment.completed"], pms="nexhealth"):
        assert extract_tokens(spec.token) == [spec.path], (
            f"{spec.token} is offered in the insert menu but the renderer does "
            f"not parse it as a token"
        )


def test_the_menu_is_scoped_to_what_the_practice_software_supplies() -> None:
    """A NexHealth clinic must not be offered fields it can never fill."""
    nexhealth = {f.path for f in fields_for_events(["appointment.completed"], pms="nexhealth")}
    gotracker = {f.path for f in fields_for_events(["appointment.completed"], pms="gotracker")}

    assert "appointment.duration_minutes" in gotracker
    assert "appointment.duration_minutes" not in nexhealth
    # The shared core is offered to both, so one campaign works on either.
    assert {"appointment.status", "appointment.start_at", "patient.first_name"} <= nexhealth
    assert {"appointment.status", "appointment.start_at", "patient.first_name"} <= gotracker


def test_the_menu_is_scoped_to_the_channel() -> None:
    voice = {f.path for f in fields_for_events(["appointment.completed"], channel="voice")}
    sms = {f.path for f in fields_for_events(["appointment.completed"], channel="sms")}
    assert voice and sms


def test_a_canonical_token_works_in_email_too() -> None:
    """Email renders through Jinja, which raises rather than blanking.

    Without the nested context in scope, `{{appointment.status}}` in an email
    body throws `UndefinedError` and the send fails outright — a harder failure
    than SMS, where the same token merely rendered empty.
    """
    from src.app.services.automation.template_renderer import build_render_vars
    from src.app.services.template_engine import render_text

    variables = build_render_vars(None, None, CONTEXT)

    assert render_text("Your appt is {{appointment.status}}", variables) == (
        "Your appt is cancelled"
    )
    assert render_text("Hi {{patient_first_name}}", variables) == "Hi Jordan"


def test_a_flat_field_still_wins_over_a_context_branch_in_email() -> None:
    variables = build_render_vars_for_test()
    from src.app.services.template_engine import render_text

    assert render_text("On {{appointment_date}}", variables) == "On September 4, 2026"


def build_render_vars_for_test() -> dict:
    from src.app.services.automation.template_renderer import build_render_vars

    return build_render_vars(None, None, CONTEXT)
