"""Static checks for the outbound automation workflow schema."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, UniqueConstraint

from src.app.database import Base
from src.app.models.automation_workflow import (
    AutomationRunStatus,
    AutomationStepStatus,
    AutomationTimerStatus,
    AutomationWorkflow,
    AutomationWorkflowEvent,
    AutomationWorkflowRun,
    AutomationWorkflowStatus,
    AutomationWorkflowStepExecution,
    AutomationWorkflowTimer,
    AutomationWorkflowVersion,
)


AUTOMATION_TABLES = {
    "automation_workflows",
    "automation_workflow_versions",
    "automation_workflow_runs",
    "automation_workflow_step_executions",
    "automation_workflow_timers",
    "automation_workflow_events",
}


def test_automation_models_are_registered_with_metadata() -> None:
    assert AUTOMATION_TABLES.issubset(Base.metadata.tables)


def test_automation_models_use_separate_namespace_from_call_workflow_statuses() -> None:
    assert AutomationWorkflow.__tablename__ == "automation_workflows"
    assert AutomationWorkflowVersion.__tablename__ == "automation_workflow_versions"
    assert AutomationWorkflowRun.__tablename__ == "automation_workflow_runs"
    assert AutomationWorkflowStepExecution.__tablename__ == "automation_workflow_step_executions"
    assert AutomationWorkflowTimer.__tablename__ == "automation_workflow_timers"
    assert AutomationWorkflowEvent.__tablename__ == "automation_workflow_events"


def test_automation_tables_are_tenant_scoped() -> None:
    for table_name in AUTOMATION_TABLES:
        table = Base.metadata.tables[table_name]
        assert "institution_id" in table.columns
        assert "location_id" in table.columns


def test_workflow_version_is_immutable_snapshot_shape() -> None:
    table = AutomationWorkflowVersion.__table__
    assert "definition" in table.columns
    assert "definition_checksum" in table.columns
    assert "content_classification" in table.columns

    unique_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert "uq_automation_workflow_versions_workflow_number" in unique_names


def test_run_and_timer_status_vocabularies_are_constrained() -> None:
    assert AutomationWorkflowStatus.ACTIVE.value == "active"
    assert AutomationRunStatus.BLOCKED.value == "blocked"
    assert AutomationStepStatus.WAITING.value == "waiting"
    assert AutomationTimerStatus.CLAIMED.value == "claimed"

    run_checks = {
        constraint.name
        for constraint in AutomationWorkflowRun.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    timer_checks = {
        constraint.name
        for constraint in AutomationWorkflowTimer.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert "ck_automation_workflow_runs_status" in run_checks
    assert "ck_automation_workflow_timers_status" in timer_checks
