"""RLS on the form-integration tables, read from the migration source.

An inbound webhook has no session and no tenant, so it resolves the clinic from
whatever the provider named — a Facebook Page id, or the form row id in the URL
we registered. That resolution happens before any tenant scoping exists, which
is the one place a mistake reads another clinic's rows.

These assertions are cheap and they fail the moment the lookup policy widens
from "exactly the row this request names" to anything else.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260902_form_integrations.py"


def _source() -> str:
    return MIGRATION.read_text()


def test_the_pre_tenant_lookup_is_select_only() -> None:
    source = _source()
    assert "ON form_provider_connections FOR SELECT" in source
    assert "ON form_definitions FOR SELECT" in source
    # Everything the lookup context can reach is a SELECT policy; it must never
    # appear in a FOR ALL grant.
    for block in source.split("CREATE POLICY")[1:]:
        if "form_webhook_lookup" in block:
            assert "FOR SELECT" in block
            assert "FOR ALL" not in block


def test_the_lookup_matches_exactly_the_row_the_request_names() -> None:
    source = _source()
    assert "account_ref = app_rls_external_id()" in source
    assert "id::text = app_rls_external_id()" in source


def test_the_verified_webhook_context_is_institution_scoped() -> None:
    source = _source()
    for block in source.split("CREATE POLICY")[1:]:
        if "_form_webhook" in block and "lookup" not in block:
            assert "institution_id = app_rls_institution_id()" in block


def test_workflows_are_readable_but_not_writable_by_a_webhook() -> None:
    """A form delivery enrolls workflows; it must not be able to edit them."""
    source = _source()
    read_only = source.split("WEBHOOK_READ_TABLES = (")[1].split(")")[0]
    assert "automation_workflows" in read_only
    assert "automation_workflow_versions" in read_only
    assert "_form_webhook_select ON {table} FOR SELECT" in source


def test_managing_a_connection_is_admin_only() -> None:
    """These rows hold a provider access token and decide where a stranger's
    contact details land."""
    source = _source()
    assert "app_rls_role() IN ('INSTITUTION_ADMIN', 'GROUP_ADMIN')" in source


def test_form_names_stay_readable_to_any_user_of_the_practice() -> None:
    """The workflow builder shows them, and a location admin edits workflows."""
    source = _source()
    assert "_tenant_select ON {table} FOR SELECT" in source


def test_every_table_forces_row_level_security() -> None:
    source = _source()
    assert "FORCE ROW LEVEL SECURITY" in source


def test_tables_are_created_idempotently() -> None:
    """The baseline's create_all has already built these on a fresh database."""
    source = _source()
    assert source.count("CREATE TABLE IF NOT EXISTS") == 4
