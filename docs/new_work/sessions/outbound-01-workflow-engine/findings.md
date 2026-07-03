# Findings: Outbound 01 - Workflow Engine

## Key Findings
- Existing `WorkflowStatus` is a human-assigned call-dashboard status feature, not the outbound automation runtime.
- Use `AutomationWorkflow*` names and `automation_*` tables for the outbound engine.
- Trigger type lives in workflow version definition JSON, not as a SQL column.
- In-flight runs stay pinned to the workflow version they enrolled under.
- Durable scheduling must be database-backed; long waits should not rely on Celery ETA/countdown alone.
- Location timezone is the v1 scheduling authority.
- Async SQLAlchemy response serialization must not lazy-load relationships or expired fields after the route session scope.

## RLS / Security Findings
- Tenant-scoped models with `institution_id` must be listed in baseline `PROTECTED_TABLES`.
- Automation tables are location-aware because runs/timers/events will eventually describe patient communication workflow state.
- Current runtime uses existing `celery` RLS context for background work.

## Dev B Dependency
- Send nodes are intentionally stubs until Dev B delivers channel handlers.
- Compliance is currently a protocol + no-op stub; real policy belongs to Plan 12.

