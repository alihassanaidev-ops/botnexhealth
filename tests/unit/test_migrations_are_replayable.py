"""Migrations must be able to build a database from scratch.

The consolidated baseline creates the whole schema with
``Base.metadata.create_all``, which produces whatever the model layer says
*today*. Every migration after it was written to transform the schema as it
stood at the time. On an existing database that is fine; on a fresh one the
baseline jumps straight to the final shape and later migrations then trip over
work that is already done.

That breakage is invisible until someone tries to stand up a new environment or
run the RLS suite, which needs a fresh database. This catches it at review time
instead. Guarded DDL is the price of the baseline working the way it does.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

VERSIONS = Path(__file__).resolve().parents[2] / "alembic" / "versions"
BASELINE = "20260510_consolidated_baseline.py"

#: Alembic operations that fail on an object the baseline already created.
GUARDABLE_OPS = {"create_table", "create_index", "add_column"}

#: Raw SQL that needs IF NOT EXISTS for the same reason.
UNGUARDED_SQL = (
    ("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"),
    ("CREATE INDEX", "CREATE INDEX IF NOT EXISTS"),
    ("CREATE UNIQUE INDEX", "CREATE UNIQUE INDEX IF NOT EXISTS"),
    ("ADD COLUMN", "ADD COLUMN IF NOT EXISTS"),
)


def _migrations():
    return sorted(p for p in VERSIONS.glob("*.py") if p.name != BASELINE)


def _upgrade_body(path: Path):
    """Only upgrade() matters: downgrade never runs while building a fresh DB."""
    tree = ast.parse(path.read_text())
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "upgrade":
            return node
    return None


def _op_calls_missing_guard(path: Path) -> list[str]:
    """op.create_table(...) and friends without if_not_exists=True."""
    upgrade = _upgrade_body(path)
    if upgrade is None:
        return []
    # A create inside an `if` has been thought about — 20260515_mfa guards its
    # tables with `if not _table_exists(...)`, which is as good as the kwarg.
    # What is dangerous is an unconditional create.
    conditional: set[int] = set()
    for node in ast.walk(upgrade):
        if isinstance(node, ast.If):
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    conditional.add(id(inner))

    bad = []
    for node in ast.walk(upgrade):
        if not isinstance(node, ast.Call) or id(node) in conditional:
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in GUARDABLE_OPS):
            continue
        if getattr(func.value, "id", None) != "op":
            continue
        if not any(k.arg == "if_not_exists" for k in node.keywords):
            bad.append(f"op.{func.attr} at line {node.lineno}")
    return bad


@pytest.mark.parametrize("path", _migrations(), ids=lambda p: p.name)
def test_alembic_operations_are_guarded(path: Path) -> None:
    unguarded = _op_calls_missing_guard(path)
    assert not unguarded, (
        f"{path.name}: {unguarded}. The baseline's create_all has already built "
        f"these on a fresh database. Pass if_not_exists=True, or use raw SQL "
        f"with IF NOT EXISTS."
    )


@pytest.mark.parametrize("path", _migrations(), ids=lambda p: p.name)
def test_raw_ddl_is_guarded(path: Path) -> None:
    upgrade = _upgrade_body(path)
    if upgrade is None:
        return
    source = ast.get_source_segment(path.read_text(), upgrade) or ""
    offenders = []
    for bare, guarded in UNGUARDED_SQL:
        # count occurrences that are not already the guarded form
        if source.count(bare) > source.count(guarded):
            offenders.append(bare)
    assert not offenders, (
        f"{path.name}: raw {offenders} without IF NOT EXISTS. On a fresh "
        f"database the baseline has already created these."
    )


def test_the_chain_has_exactly_one_head() -> None:
    """A forked history cannot be applied at all — it has bitten this repo before."""
    revisions, downs = set(), set()
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            name = getattr(node.targets[0], "id", None)
            if name == "revision" and isinstance(node.value, ast.Constant):
                revisions.add(node.value.value)
            elif name == "down_revision":
                if isinstance(node.value, ast.Constant) and node.value.value:
                    downs.add(node.value.value)
                elif isinstance(node.value, (ast.Tuple, ast.List)):
                    downs.update(
                        e.value for e in node.value.elts if isinstance(e, ast.Constant)
                    )
    heads = revisions - downs
    assert len(heads) == 1, f"migration history has forked: {sorted(heads)}"
