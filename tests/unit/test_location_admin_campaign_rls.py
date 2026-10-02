"""Static contract for location-scoped campaign RLS hardening."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MIGRATION = (
    ROOT
    / "alembic"
    / "versions"
    / "20260908_location_admin_campaign_rls.py"
)


def test_location_admin_campaign_rls_chains_from_live_staging_head() -> None:
    source = MIGRATION.read_text()

    assert 'down_revision = "20260907_legacy_triggers"' in source


def test_location_admin_guard_is_exact_location_and_restrictive() -> None:
    source = MIGRATION.read_text()

    assert "AS RESTRICTIVE FOR ALL" in source
    assert "app_rls_role() <> 'LOCATION_ADMIN'" in source
    assert "app_rls_location_id() IS NOT NULL" in source
    assert ".location_id = app_rls_location_id()" in source
    for table in (
        "automation_workflows",
        "automation_workflow_versions",
        "automation_workflow_runs",
        "automation_workflow_step_executions",
        "automation_workflow_drip_states",
        "automation_workflow_split_assignments",
        "automation_workflow_timers",
        "automation_workflow_events",
        "workflow_voice_attempts",
        "campaign_audience_definitions",
        "campaign_audience_previews",
        "campaign_response_events",
        "campaign_staff_handoffs",
        "campaign_conversation_threads",
        "campaign_metrics_daily",
        "campaign_split_metrics_daily",
        "patient_workflow_status_events",
        "outbound_email_messages",
        "quiet_hours_exceptions",
    ):
        assert f'"{table}"' in source


def test_workflow_schedules_gain_force_rls() -> None:
    source = MIGRATION.read_text()

    assert "ALTER TABLE workflow_schedules ENABLE ROW LEVEL SECURITY" in source
    assert "ALTER TABLE workflow_schedules FORCE ROW LEVEL SECURITY" in source
    assert "CREATE POLICY workflow_schedules_rls" in source
    assert "workflow_schedules.location_id = app_rls_location_id()" in source
