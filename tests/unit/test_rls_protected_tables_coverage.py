"""Verify every model with institution_id is protected by RLS."""
from __future__ import annotations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_all_institution_scoped_models_are_in_protected_tables() -> None:
    """Models with institution_id must be in PROTECTED_TABLES.

    Drift catch: a new PHI-ish model added without updating
    PROTECTED_TABLES would silently lack RLS.
    """
    # Import all models so SQLAlchemy mappers are registered
    from src.app.database import Base
    import src.app.models  # noqa: F401 - registration side-effect
    from src.app.models.user import User  # noqa: F401

    institution_scoped_tables: set[str] = set()
    for mapper in Base.registry.mappers:
        table = mapper.local_table
        if table is None:
            continue
        if "institution_id" in table.columns:
            institution_scoped_tables.add(table.name)

    # Import the consolidated baseline migration to read PROTECTED_TABLES
    # via importlib (so its top-level imports — Base, models — resolve
    # without re-defining mappers in a stray globals dict).
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_baseline_migration",
        ROOT / "alembic" / "versions" / "20260510_consolidated_baseline.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    protected = set(mod.PROTECTED_TABLES)

    # Tables added after the baseline enable RLS in their OWN migration (the
    # baseline's ALTER TABLE loop can't cover a table that doesn't exist yet).
    # A table is protected if any migration declares its ``{table}_rls`` policy.
    versions_src = "\n".join(
        p.read_text()
        for p in (ROOT / "alembic" / "versions").glob("*.py")
    )
    protected |= {
        table
        for table in institution_scoped_tables
        if f"{table}_rls" in versions_src
    }

    missing = institution_scoped_tables - protected
    assert not missing, (
        f"Models with institution_id lack an RLS policy: {missing}. "
        f"Add the table to PROTECTED_TABLES in the baseline migration, or "
        f"enable FORCE RLS + a {{table}}_rls policy in its own migration."
    )
