# Progress: Outbound 01 - Workflow Engine

## Slice 1 - Schema Foundation
- **Status:** complete
- Added automation workflow tables, models, migration, RLS policies, grants, indexes, and static tests.
- Fixed Alembic revision length issue by shortening revision id to `20260702_auto_workflow_core`.
- Added `workflow_statuses` to baseline RLS protected table list after static coverage exposed existing drift.

## Slice 2 - Engine Service Skeleton
- **Status:** complete
- Added definition, enrollment, scheduler, and runtime services.
- Added unit tests for state transitions, idempotency, timers, and event emission.

## Slice 3 - Definition Schema
- **Status:** complete
- Added Pydantic workflow definition schema with triggers, wait/send/condition/exit nodes, validation, and publish-time validation.

## Slice 4 - Dispatcher + Scheduler Tasks
- **Status:** complete
- Added step dispatcher and Celery timer polling/dispatch tasks.
- Send steps are still stubs by design.

## Slice 5 - Compliance Gate Stub
- **Status:** complete
- Added compliance gate protocol, `GateResult`, and `NoOpComplianceGate`.
- Dispatcher checks the gate before send nodes.

## Slice 6 - API Routes
- **Status:** complete for current engine scope
- Added workflow CRUD/lifecycle/enrollment/run endpoints.
- Fixed response-loading bugs discovered during UI verification:
  - eager-load `current_version`
  - build responses while session is open
  - refresh `updated_at` after lifecycle mutations
  - fix workflow lookup argument order

## Verification
- Focused automation route and definition tests: `29 passed, 1 warning`.
- Full suite observed by user: `1280 passed, 16 skipped, 8 unrelated failures`.

