"""Regression pins for contact consolidation and the discovered RLS gaps."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260902_contact_rls_hardening.py"


def test_migration_follows_current_head() -> None:
    source = MIGRATION.read_text()
    assert 'down_revision = "20260902_enquiry_intake_lookup"' in source


def test_legacy_people_are_backfilled_before_enquiry_table_is_removed() -> None:
    source = MIGRATION.read_text()
    assert source.index("INSERT INTO contacts") < source.index(
        'op.execute("DROP TABLE campaign_enquiries")'
    )
    assert "INSERT INTO contact_location_accesses" in source


def test_intake_can_only_read_its_scoped_workflow_definition() -> None:
    source = MIGRATION.read_text()
    assert "automation_workflows_enquiry_intake_select" in source
    assert "automation_workflow_versions_enquiry_intake_select" in source
    assert "app_rls_context_type() = 'enquiry_intake'" in source
    assert "institution_id = app_rls_institution_id()" in source


def test_runtime_role_loses_direct_audit_partition_access() -> None:
    source = MIGRATION.read_text()
    assert "REVOKE ALL PRIVILEGES ON TABLE %s FROM nexhealth_app" in source
