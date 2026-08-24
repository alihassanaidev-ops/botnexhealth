"""Static safety checks for the Twilio SMS correlation repair migration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260825_twilio_sms_rls.py"


def test_twilio_sms_repair_chains_after_current_head() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260825_twilio_sms_rls"' in source
    assert 'down_revision = "20260824_remove_reply_keys"' in source


def test_twilio_sms_repair_grants_only_scoped_workflow_reads() -> None:
    source = MIGRATION.read_text()

    for table in (
        "automation_workflow_runs",
        "automation_workflow_versions",
        "automation_workflow_step_executions",
    ):
        assert f'"{table}"' in source
        assert f"{table}_twilio_select" in source

    assert "FOR SELECT" in source
    assert "app_rls_context_type() = 'twilio'" in source
    assert "institution_id = app_rls_institution_id()" in source
    assert "location_id = app_rls_location_id()" in source


def test_twilio_sms_repair_allows_inbound_reply_notifications() -> None:
    source = MIGRATION.read_text()

    assert "DROP CONSTRAINT IF EXISTS ck_notifications_type" in source
    assert '"inbound_sms_reply"' in source
    assert "VALIDATE CONSTRAINT ck_notifications_type" in source
