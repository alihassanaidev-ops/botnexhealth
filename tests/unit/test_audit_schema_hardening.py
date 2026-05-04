"""Static checks for audit-log hardening DDL in the consolidated baseline migration."""

from pathlib import Path

from src.app.models.audit_log import AuditActor


ROOT = Path(__file__).resolve().parents[2]
BASELINE_MIGRATION = (
    ROOT / "alembic" / "versions" / "20260510_consolidated_baseline.py"
)


def test_audit_log_immutability_blocks_update_and_delete() -> None:
    migration_sql = BASELINE_MIGRATION.read_text()

    assert "CREATE OR REPLACE FUNCTION prevent_audit_log_mutation()" in migration_sql
    assert "audit_logs table is append-only" in migration_sql
    assert "CREATE TRIGGER audit_logs_no_update" in migration_sql
    assert "BEFORE UPDATE ON audit_logs" in migration_sql
    assert "CREATE TRIGGER audit_logs_no_delete" in migration_sql
    assert "BEFORE DELETE ON audit_logs" in migration_sql


def test_audit_actor_check_matches_audit_actor_enum() -> None:
    migration_sql = BASELINE_MIGRATION.read_text()

    assert "audit_logs_actor_check" in migration_sql
    for actor in AuditActor:
        assert f"'{actor.value}'" in migration_sql
