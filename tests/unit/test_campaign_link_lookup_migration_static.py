from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260902_campaign_link_run_lookup.py"


def test_campaign_link_lookup_is_select_only_and_exact_run_scoped() -> None:
    source = MIGRATION.read_text()

    assert "ON automation_workflow_runs FOR SELECT" in source
    assert "app_rls_context_type() = 'campaign_link_lookup'" in source
    assert "automation_workflow_runs.id::text = app_rls_external_id()" in source
    assert "FOR ALL" not in source
