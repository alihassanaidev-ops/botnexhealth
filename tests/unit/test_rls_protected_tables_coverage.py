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

    namespace: dict[str, object] = {}
    exec(
        (ROOT / "alembic" / "versions" / "20260506_rls_full_staged.py").read_text(),
        namespace,
    )
    protected = set(namespace["PROTECTED_TABLES"])

    missing = institution_scoped_tables - protected
    assert not missing, (
        f"Models with institution_id not in PROTECTED_TABLES: {missing}. "
        f"Add them to alembic/versions/20260506_rls_full_staged.py PROTECTED_TABLES."
    )
