from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260506_rls_full_staged.py"


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


def test_rls_migration_adds_scope_columns_and_contact_access_table() -> None:
    source = MIGRATION.read_text()

    assert '"calls"' in source and '"location_id"' in source
    assert '"sms_history_logs"' in source and '"institution_id"' in source
    assert "contact_location_accesses" in source
    assert "uq_contact_location_access_contact_location" in source
