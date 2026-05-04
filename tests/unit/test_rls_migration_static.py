from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260510_consolidated_baseline.py"


def test_rls_migration_protects_expected_tables() -> None:
    namespace: dict[str, object] = {}
    exec(MIGRATION.read_text(), namespace)
    protected = set(namespace["PROTECTED_TABLES"])

    assert {
        "institutions",
        "institution_locations",
        "users",
        "contacts",
        "contact_location_accesses",
        "calls",
        "custom_field_values",
        "notifications",
        "sms_history_logs",
        "audit_logs",
        "dead_letter_events",
        "retell_webhook_events",
        "retell_function_invocations",
    }.issubset(protected)


def test_rls_migration_contains_policy_and_force_rls_operations() -> None:
    source = MIGRATION.read_text()

    assert "ENABLE ROW LEVEL SECURITY" in source
    assert "FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY" in source
    assert "app_rls_context_type" in source
    assert "app_rls_institution_id" in source
    assert "app_rls_location_id" in source
    assert "app_rls_external_id" in source


def test_rls_migration_references_scope_tables_and_columns() -> None:
    """The consolidated baseline owns both schema (via create_all) and
    RLS DDL. Scope columns and the contact-access bridge table come from
    models — verify the migration's POLICIES and PROTECTED_TABLES still
    name them so a model rename without a migration update is caught.
    """
    source = MIGRATION.read_text()

    # location_id appears in calls policy + custom values policy
    assert "calls.location_id" in source
    # sms_history_logs.institution_id and location_id appear in its policy
    assert "sms_history_logs.institution_id" in source
    assert "sms_history_logs.location_id" in source
    # contact_location_accesses appears in PROTECTED_TABLES + its policy
    assert "contact_location_accesses" in source
