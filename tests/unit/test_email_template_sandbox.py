"""The email template environment must be sandboxed.

Institution admins author these templates through the dashboard
(``PUT /api/institution/email-templates/{type}``) and render arbitrary
submitted content immediately via ``POST /preview/live``, so template
expressions run on attacker-influenced input by design. A plain jinja2
``Environment`` — even with no filesystem loader — still permits attribute
traversal out of the template context.
"""

from __future__ import annotations

import pytest
from jinja2.exceptions import SecurityError

from src.app.services.email_template_service import EmailTemplateService


def test_attribute_traversal_yields_nothing():
    """A blocked attribute resolves to Undefined rather than the real object, so
    nothing about the runtime leaks into the rendered email."""
    out = EmailTemplateService.render("{{ ''.__class__ }}", {})
    assert out == ""


def test_attribute_traversal_on_a_variable_yields_nothing():
    out = EmailTemplateService.render("{{ x.__class__ }}", {"x": "a"})
    assert out == ""


def test_chained_traversal_raises():
    """Going deeper than one hop is refused outright."""
    with pytest.raises(SecurityError):
        EmailTemplateService.render("{{ ''.__class__.__mro__ }}", {})


def test_globals_traversal_raises():
    with pytest.raises(SecurityError):
        EmailTemplateService.render(
            "{{ obj.__init__.__globals__ }}", {"obj": object()}
        )


# ---------------------------------------------------------------------------
# Normal templating must be unaffected — the sandbox is not allowed to cost the
# clinics any authoring capability.
# ---------------------------------------------------------------------------


def test_variable_substitution_still_works():
    assert EmailTemplateService.render("Hi {{ name }}", {"name": "Jane"}) == "Hi Jane"


def test_conditionals_still_work():
    out = EmailTemplateService.render(
        "{% if booked %}See you soon{% else %}Book now{% endif %}", {"booked": True}
    )
    assert out == "See you soon"


def test_loops_still_work():
    out = EmailTemplateService.render(
        "{% for i in items %}{{ i }},{% endfor %}", {"items": ["a", "b"]}
    )
    assert out == "a,b,"


def test_filters_still_work():
    assert EmailTemplateService.render("{{ name|upper }}", {"name": "jane"}) == "JANE"


def test_autoescape_still_applies():
    """Patient-supplied values must not be able to inject markup."""
    out = EmailTemplateService.render("{{ name }}", {"name": "<script>x</script>"})
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_variable_named_self_does_not_crash_rendering():
    """``Template.render`` takes ``self``, so passing the context as ``**kwargs``
    made a variable of that name raise TypeError mid-send. Passing a dict avoids
    the crash. ``self`` is reserved by Jinja so the value is still shadowed —
    the point of the test is that a send no longer blows up on it."""
    out = EmailTemplateService.render("Hi {{ name }}", {"self": "x", "name": "Jane"})
    assert out == "Hi Jane"


def test_validate_template_still_reports_syntax_errors():
    assert EmailTemplateService.validate_template("{% if %}") is not None
    assert EmailTemplateService.validate_template("Hi {{ name }}") is None
