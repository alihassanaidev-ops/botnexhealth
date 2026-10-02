"""One sandboxed template engine for all outbound content.

Two environments, differing only in escaping:

``text``
    Plain-text email bodies and subjects. **autoescape off** — escaping here
    would turn a patient named "Tom & Jerry" into "Tom &amp; Jerry" in a
    plain-text message.

``html``
    HTML email bodies. **autoescape on** — rendered patient data must not be
    able to inject markup.

Both are :class:`~jinja2.sandbox.SandboxedEnvironment`: templates are authored
by institution admins through the dashboard, so expressions run on
attacker-influenced input and must not be able to traverse out of the template
context.

Undefined variables render as an empty string, matching the legacy
``render_sms_body`` substitution so patients never see a raw placeholder.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from jinja2 import BaseLoader, TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger(__name__)

_text_env = SandboxedEnvironment(loader=BaseLoader(), autoescape=False)
_html_env = SandboxedEnvironment(loader=BaseLoader(), autoescape=True)

# The pre-Jinja substitution. Retained as a fallback: Jinja rejects content the
# regex simply ignored (``50{{ % off }}``), and a template that has been sending
# happily for months must not start failing mid-campaign because the engine
# under it changed.
_LEGACY_VAR_RE = re.compile(r"\{\{\s*(\w+)\s*\}\}")


def _legacy_render(template: str, variables: dict[str, Any]) -> str:
    def _replace(match: re.Match) -> str:
        return str(variables.get(match.group(1), "") or "")

    return _LEGACY_VAR_RE.sub(_replace, template)


def _render(env: SandboxedEnvironment, template: str, variables: dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return env.from_string(template).render(variables)
    except TemplateSyntaxError as exc:
        # Not an error the recipient should ever see. Fall back to the literal
        # substitution that was in use before, and surface it for the operator.
        logger.warning(
            "template syntax rejected by Jinja, falling back to literal "
            "substitution: line=%s error=%s",
            exc.lineno,
            exc.message,
        )
        return _legacy_render(template, variables)


def render_text(template: str, variables: dict[str, Any]) -> str:
    """Render a plain-text template. No HTML escaping."""
    return _render(_text_env, template, variables)


def render_html(template: str, variables: dict[str, Any]) -> str:
    """Render an HTML template. Values are HTML-escaped."""
    return _render(_html_env, template, variables)


def validate(template: str) -> str | None:
    """Return a human-readable syntax error, or None when the template parses."""
    try:
        _text_env.parse(template)
        return None
    except TemplateSyntaxError as exc:
        return f"Line {exc.lineno}: {exc.message}" if exc.lineno else str(exc.message)
