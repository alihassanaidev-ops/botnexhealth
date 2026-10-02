"""Unit tests for the shared sandboxed template engine.

The two environments differ only in escaping, and getting that asymmetry wrong
is silently damaging in both directions: escaping a plain-text body mangles it
for the patient, and not escaping HTML lets rendered patient data inject markup.
"""

from __future__ import annotations

import pytest
from jinja2.exceptions import SecurityError

from src.app.services.template_engine import render_html, render_text, validate


# ---------------------------------------------------------------------------
# Escaping asymmetry
# ---------------------------------------------------------------------------


def test_text_is_not_escaped():
    """A patient named "Tom & Jerry" must not read as "Tom &amp; Jerry"."""
    assert render_text("{{ name }}", {"name": "Tom & Jerry"}) == "Tom & Jerry"


def test_text_preserves_angle_brackets():
    assert render_text("{{ v }}", {"v": "<3"}) == "<3"


def test_html_is_escaped():
    out = render_html("{{ name }}", {"name": "<script>x</script>"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_html_escapes_ampersands():
    assert render_html("{{ name }}", {"name": "Tom & Jerry"}) == "Tom &amp; Jerry"


# ---------------------------------------------------------------------------
# Sandboxing — both environments
# ---------------------------------------------------------------------------


def test_text_env_is_sandboxed():
    with pytest.raises(SecurityError):
        render_text("{{ ''.__class__.__mro__ }}", {})


def test_html_env_is_sandboxed():
    with pytest.raises(SecurityError):
        render_html("{{ ''.__class__.__mro__ }}", {})


# ---------------------------------------------------------------------------
# Compatibility with the substitution it replaces
# ---------------------------------------------------------------------------


def test_undefined_renders_empty():
    """Matches the legacy substitution — patients never see a placeholder."""
    assert render_text("a{{ missing }}b", {}) == "ab"


def test_simple_substitution_matches_legacy_behaviour():
    assert render_text("Hi {{ name }}", {"name": "Jane"}) == "Hi Jane"


def test_whitespace_inside_braces_is_tolerated():
    assert render_text("Hi {{name}}", {"name": "Jane"}) == "Hi Jane"


def test_conditionals_and_filters_work():
    assert render_text("{% if x %}{{ x|upper }}{% endif %}", {"x": "hi"}) == "HI"


def test_empty_template_renders_empty():
    assert render_text("", {"a": 1}) == ""


# ---------------------------------------------------------------------------
# Fallback — content Jinja rejects but the old substitution tolerated
# ---------------------------------------------------------------------------


def test_stray_braces_fall_back_instead_of_raising():
    """A template that has been sending for months must not start failing
    mid-campaign because the engine under it changed."""
    out = render_text("50{{ % off }} for {{ name }}", {"name": "Jane"})
    assert "Jane" in out


def test_fallback_still_substitutes_known_variables():
    out = render_text("{{ name }} — {{ % }}", {"name": "Jane"})
    assert out.startswith("Jane")


def test_fallback_drops_unknown_variables():
    out = render_text("{{ nope }}{{ % }}", {})
    assert "nope" not in out


# ---------------------------------------------------------------------------
# validate()
# ---------------------------------------------------------------------------


def test_validate_accepts_good_template():
    assert validate("Hi {{ name }}") is None


def test_validate_reports_bad_syntax():
    assert validate("{% if %}") is not None
