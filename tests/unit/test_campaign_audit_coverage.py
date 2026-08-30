"""Item 32 · no state-changing campaign operation may go unaudited.

Static (AST) rather than runtime by design: it needs no database, no settings
and no app import, so it stays green in any checkout and fails loudly the
moment someone adds a campaign endpoint without an audit decorator.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROUTES = Path(__file__).resolve().parents[2] / "src" / "app" / "api" / "routes"
CAMPAIGN_ROUTE_FILES = ("automation_workflows.py", "automation_templates.py")
MUTATING_VERBS = {"post", "put", "patch", "delete"}

# Endpoints that use a mutating verb but change no state: they validate,
# simulate or preview against caller-supplied input and touch no stored record.
# Anything added here needs a reason in this comment, not just a name.
NON_MUTATING_ALLOWLIST = {
    "validate_definition",       # validates a definition body, stores nothing
    "dry_run_definition",        # simulates against a caller-supplied context
    "preview_launch_checklist",  # read-only readiness preview
}


def _endpoints(path: Path):
    """Yield (function_name, http_verb, audited_action | None) per route."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if not isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)):
            continue
        verb = None
        action = None
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            func = dec.func
            if isinstance(func, ast.Attribute) and getattr(func.value, "id", "") == "router":
                verb = func.attr
            elif isinstance(func, ast.Name) and func.id == "audit":
                if dec.args and isinstance(dec.args[0], ast.Attribute):
                    action = dec.args[0].attr
        if verb:
            yield node.name, verb, action


@pytest.mark.parametrize("filename", CAMPAIGN_ROUTE_FILES)
def test_every_state_changing_campaign_endpoint_is_audited(filename: str) -> None:
    unaudited = [
        name
        for name, verb, action in _endpoints(ROUTES / filename)
        if verb in MUTATING_VERBS
        and name not in NON_MUTATING_ALLOWLIST
        and action is None
    ]
    assert not unaudited, (
        f"{filename}: state-changing campaign endpoints with no @audit decorator: "
        f"{unaudited}. Add one, or add the name to NON_MUTATING_ALLOWLIST with a "
        f"reason if it genuinely changes no state."
    )


@pytest.mark.parametrize("filename", CAMPAIGN_ROUTE_FILES)
def test_campaign_endpoints_use_campaign_action_types(filename: str) -> None:
    """Actions come from the controlled list — no borrowed or free-text names."""
    wrong = [
        (name, action)
        for name, _verb, action in _endpoints(ROUTES / filename)
        if action is not None and not action.startswith("CAMPAIGN_")
    ]
    assert not wrong, f"{filename}: non-campaign audit actions on campaign routes: {wrong}"


def test_publish_and_enrol_are_distinguishable() -> None:
    """Publishing and activating must be separate entries in the trail."""
    actions = {
        name: action
        for name, _v, action in _endpoints(ROUTES / "automation_workflows.py")
    }
    assert actions.get("publish_workflow") == "CAMPAIGN_PUBLISH"
    assert actions.get("resume_workflow") == "CAMPAIGN_RESUME"
    assert actions.get("enroll_in_workflow") == "CAMPAIGN_ENROLL"
    assert actions.get("bulk_enroll") == "CAMPAIGN_BULK_ENROLL"
    assert actions["publish_workflow"] != actions["resume_workflow"]
