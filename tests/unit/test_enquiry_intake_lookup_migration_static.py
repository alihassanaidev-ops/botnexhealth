from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260902_enquiry_intake_lookup.py"


def test_intake_lookup_is_select_only_and_exact_token_scoped() -> None:
    source = MIGRATION.read_text()

    assert "ON enquiry_intake_sources FOR SELECT" in source
    assert "app_rls_context_type() = 'enquiry_intake_lookup'" in source
    assert "token_hash = app_rls_external_id()" in source
    assert "FOR ALL" not in source
