"""Static safety checks for automation workflow migration DDL."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260702_auto_workflow_core.py"
BASELINE = ROOT / "alembic" / "versions" / "20260510_consolidated_baseline.py"

AUTOMATION_TABLES = {
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_runs",
    "automation_workflow_step_executions",
    "automation_workflow_timers",
    "automation_workflow_events",
}


def test_automation_migration_chains_after_current_head() -> None:
    source = MIGRATION.read_text()

    assert 'down_revision = "20260622_nopms_call_status"' in source


def test_automation_tables_are_rls_protected_in_migration_and_baseline() -> None:
    migration_source = MIGRATION.read_text()
    baseline_source = BASELINE.read_text()

    for table in AUTOMATION_TABLES:
        assert f'"{table}"' in migration_source
        assert f'"{table}"' in baseline_source

    assert "for table in AUTOMATION_TABLES:" in migration_source
    assert "_enable_rls(table)" in migration_source
    assert "ENABLE ROW LEVEL SECURITY" in migration_source
    assert "FORCE ROW LEVEL SECURITY" in migration_source
    assert "CREATE POLICY {table}_rls" in migration_source
    assert "app_rls_context_type() IN ('celery', 'dead_letter')" in migration_source
    assert "app_rls_location_id() IS NULL" in migration_source


def test_automation_migration_has_scheduler_and_idempotency_indexes() -> None:
    source = MIGRATION.read_text()

    assert "uq_automation_workflow_run_idempotency" in source
    assert "WHERE idempotency_key IS NOT NULL" in source
    assert "ix_automation_workflow_timers_due" in source
    assert "uq_automation_timer_active_step" in source
    assert "status IN ('pending', 'claimed')" in source
