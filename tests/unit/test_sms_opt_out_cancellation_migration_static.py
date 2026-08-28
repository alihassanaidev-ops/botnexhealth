"""Static safety checks for tenant-scoped Twilio STOP cancellation policies."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "alembic" / "versions" / "20260825_sms_optout_cancel.py"


def test_sms_opt_out_cancellation_migration_chains_after_current_head() -> None:
    source = MIGRATION.read_text()

    assert 'revision = "20260825_sms_optout_cancel"' in source
    assert 'down_revision = "20260825_sms_suppress_location"' in source
    assert len("20260825_sms_optout_cancel") <= 32


def test_sms_opt_out_cancellation_grants_only_scoped_required_writes() -> None:
    source = MIGRATION.read_text()

    assert "automation_workflow_runs_twilio_update" in source
    assert "automation_workflow_timers_twilio_select" in source
    assert "automation_workflow_timers_twilio_update" in source
    assert "automation_workflow_events_twilio_insert" in source
    assert "automation_workflow_events_twilio_select_cancelled" in source
    assert "FOR UPDATE" in source
    assert "FOR INSERT" in source
    assert "event_type = 'run.cancelled'" in source
    assert "app_rls_context_type() = 'twilio'" in source
    assert "institution_id = app_rls_institution_id()" in source
    assert "location_id = app_rls_location_id()" in source
