"""Every symbol a Celery task imports lazily must actually exist.

`tasks/automation_workflow.py` does most of its importing inside function bodies
to keep worker start-up cheap and to break import cycles. The cost is that a
wrong name is invisible: the module imports fine, the test suite passes, and the
failure only appears when that task first runs in production.

That is exactly how `AudienceService` — which is really
`CampaignAudienceService` — reached staging and made the schedule tick fail
every sixty seconds. These tests resolve the deferred imports so a typo is a
test failure rather than a log full of retries.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

TASK_MODULE = pathlib.Path("src/app/tasks/automation_workflow.py")


def _deferred_imports() -> list[tuple[str, str, int]]:
    """(module, symbol, lineno) for every `from x import y` inside a function."""
    tree = ast.parse(TASK_MODULE.read_text())
    found: list[tuple[str, str, int]] = []
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(parent):
            if isinstance(node, ast.ImportFrom) and node.module:
                if not node.module.startswith("src.app"):
                    continue
                for alias in node.names:
                    found.append((node.module, alias.name, node.lineno))
    return sorted(set(found))


DEFERRED = _deferred_imports()


def test_the_task_module_actually_defers_imports() -> None:
    """Guard the guard: if this stops finding any, the test above is vacuous."""
    assert len(DEFERRED) > 10


@pytest.mark.parametrize(
    "module,symbol,lineno",
    DEFERRED,
    ids=[f"{m.rsplit('.', 1)[-1]}.{s}:{n}" for m, s, n in DEFERRED],
)
def test_a_deferred_import_resolves(module: str, symbol: str, lineno: int) -> None:
    imported = importlib.import_module(module)
    assert hasattr(imported, symbol), (
        f"{TASK_MODULE}:{lineno} imports '{symbol}' from '{module}', "
        f"which does not define it. This only fails when the task runs."
    )
