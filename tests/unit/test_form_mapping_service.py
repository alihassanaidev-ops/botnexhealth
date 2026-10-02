"""What a form's questions mean, and what a submission is allowed to carry.

Two behaviours are load-bearing and easy to regress:

* the default mapping proposes an identifier only when the question's *declared
  type* says so, or the wording says so on a free-text question — never from the
  shape of an answer, which is how a phone number typed into a comment box ends
  up being texted;
* answers mapped to a contact column, or to a custom field the practice marked
  private, never reach a workflow's run context.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.app.models.form_integration import FormFieldTarget
from src.app.services.forms.mapping_service import (
    apply_mapping,
    build_default_mapping,
    default_contact_field,
    slugify,
)
from src.app.services.forms.providers.base import ProviderFormField


def _mapping(
    source_key: str,
    *,
    kind: str,
    contact_field: str | None = None,
    custom_field_id: str | None = None,
    context_key: str | None = None,
):
    return SimpleNamespace(
        id=f"map-{source_key}",
        source_key=source_key,
        target_kind=kind,
        target_contact_field=contact_field,
        target_custom_field_id=custom_field_id,
        context_key=context_key,
    )


def _definition(field_key: str, *, is_phi: bool = False, field_type: str = "text"):
    return SimpleNamespace(
        id=f"def-{field_key}",
        field_key=field_key,
        field_type=field_type,
        is_phi=is_phi,
    )


# ── defaults ────────────────────────────────────────────────────────────
def test_declared_type_wins_over_wording() -> None:
    field = ProviderFormField(key="q1", label="What is bothering you?", type="email")
    assert default_contact_field(field) == "email"


def test_wording_only_applies_to_free_text() -> None:
    """A multiple-choice question named "Phone or email?" is not a phone number."""
    choice = ProviderFormField(
        key="q1", label="Phone or email?", type="multiple_choice"
    )
    assert default_contact_field(choice) is None

    text = ProviderFormField(key="q2", label="Mobile number", type="short_text")
    assert default_contact_field(text) == "phone"


def test_first_name_is_not_swallowed_by_the_bare_name_pattern() -> None:
    field = ProviderFormField(key="q1", label="First name", type="short_text")
    assert default_contact_field(field) == "first_name"


def test_a_qualification_question_starts_unmapped() -> None:
    """Conservative on purpose: unmapped is visible, a wrong guess is not."""
    field = ProviderFormField(
        key="problem", label="What's bothering you?", type="short_text"
    )
    mapping = build_default_mapping(
        institution_id="inst-1", form_id="form-1", source=field
    )
    assert mapping.target_kind == FormFieldTarget.IGNORE.value
    assert mapping.target_contact_field is None


# ── applying a mapping ──────────────────────────────────────────────────
def test_contact_answers_never_reach_the_run_context() -> None:
    result = apply_mapping(
        answers={"email": "person@example.com", "problem": "Toothache"},
        mappings=[
            _mapping("email", kind="contact_field", contact_field="email"),
            _mapping("problem", kind="custom_field", custom_field_id="def-problem"),
        ],
        definitions={"def-problem": _definition("problem")},
    )
    assert result.contact_fields == {"email": "person@example.com"}
    assert result.context_answers == {"problem": "Toothache"}
    assert "person@example.com" not in str(result.context_answers)


def test_a_private_custom_field_is_stored_but_not_branchable() -> None:
    result = apply_mapping(
        answers={"dob": "1990-01-01"},
        mappings=[_mapping("dob", kind="custom_field", custom_field_id="def-dob")],
        definitions={"def-dob": _definition("dob", is_phi=True)},
    )
    assert [d.field_key for d, _ in result.custom_field_values] == ["dob"]
    assert result.context_answers == {}


def test_boolean_answers_are_normalised_for_comparison() -> None:
    """`Yes` and `true` must compare the same, or every author writes both."""
    result = apply_mapping(
        answers={"visited": "Yes"},
        mappings=[
            _mapping("visited", kind="custom_field", custom_field_id="def-visited")
        ],
        definitions={
            "def-visited": _definition("visited", field_type="boolean")
        },
    )
    assert result.context_answers == {"visited": True}
    # The stored value keeps what the person actually picked.
    assert result.custom_field_values[0][1] == "Yes"


def test_multi_select_keeps_every_pick() -> None:
    result = apply_mapping(
        answers={"symptoms": ["Pain", "Swelling"]},
        mappings=[
            _mapping("symptoms", kind="custom_field", custom_field_id="def-symptoms")
        ],
        definitions={"def-symptoms": _definition("symptoms")},
    )
    assert result.custom_field_values[0][1] == "Pain, Swelling"
    assert result.context_answers == {"symptoms": ["Pain", "Swelling"]}


def test_unmapped_answers_are_dropped_and_reported() -> None:
    result = apply_mapping(
        answers={"nickname": "Bob"}, mappings=[], definitions={}
    )
    assert result.unmapped_keys == ["nickname"]
    assert result.contact_fields == {}
    assert result.context_answers == {}


def test_an_ignored_question_is_not_reported_as_unmapped() -> None:
    """"Ignore" is a decision the practice made; it is not an omission."""
    result = apply_mapping(
        answers={"nickname": "Bob"},
        mappings=[_mapping("nickname", kind=FormFieldTarget.IGNORE.value)],
        definitions={},
    )
    assert result.unmapped_keys == []


def test_a_mapping_pointing_at_a_deleted_custom_field_drops_the_answer() -> None:
    result = apply_mapping(
        answers={"problem": "Toothache"},
        mappings=[
            _mapping("problem", kind="custom_field", custom_field_id="def-gone")
        ],
        definitions={},
    )
    assert result.custom_field_values == []
    assert result.context_answers == {}


def test_slugify_produces_a_stable_key() -> None:
    assert slugify("What's bothering you?") == "what_s_bothering_you"
    assert slugify("") == "answer"
