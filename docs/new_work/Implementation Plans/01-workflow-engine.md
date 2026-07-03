# Part 1 - Workflow Engine Implementation Plan

## What Needs To Be Built

Build the dynamic, multi-tenant, timezone-aware workflow runtime that executes outbound engagement campaigns. The engine must support triggers, conditions, waits, actions, enrollment, durable scheduling, retries, exactly-one dispatch semantics, workflow versions, and observable per-contact sequence runs.

This is the foundation for outbound voice, SMS, email, callbacks, the four launch campaigns, and future campaign types. Campaigns should be configuration executed by this engine, not hard-coded business flows.

## Existing System Context

The backend already has:

- FastAPI route/service/model organization under `src/app`.
- Async SQLAlchemy models with UUID primary keys and Alembic migrations.
- Tenant isolation through `institution_id`, optional `location_id`, application dependencies, and Postgres RLS.
- Location timezone on `InstitutionLocation.timezone`.
- Celery workers with Redis broker for immediate async jobs.
- EventBridge -> Fargate scheduled jobs for recurring batch scripts.
- Dead-letter capture, audit logging, PHI encryption, retention policy, and PHI-safe logging patterns.
- Existing Retell function idempotency and Retell webhook idempotency patterns.
- Existing SSE event bus for lightweight tenant-scoped update hints.
- Existing workflow status feature, but this is only human-defined call statuses, not a workflow automation runtime.

Current gaps:

- No durable timer system exists for "resume this run at clinic-local time later."
- No workflow definition schema, workflow versioning model, run model, step state, trigger registry, or action registry exists.
- No quiet-hours/send-window enforcement exists.
- No cross-channel run coordination exists.
- No workflow-level compliance validator exists.

## Existing Components To Reuse

- `Institution` and `InstitutionLocation` as tenant and execution context.
- Existing RLS migration/static-test conventions for all new tenant-scoped tables.
- Existing audit service for publish/pause/configuration and PHI-touching actions.
- Existing dead-letter service for failed step dispatch and replayable vendor failures.
- Existing Celery worker conventions for short-lived async work.
- Existing EventBridge scheduled-job pattern for coarse recurring jobs and reconciliation.
- Existing Redis usage for distributed coordination, SSE, rate limiting, and locks.
- Existing Retell/PMS/SMS/email service patterns as action implementations.

## New Components Required

### Data Model

Add workflow runtime tables through Alembic:

- `workflows`
  - `institution_id`, `location_id`
  - name, description, category/campaign type
  - status: `draft`, `active`, `paused`, `archived`
  - current published version id
  - template source id when cloned from a system template
  - created/updated/published metadata

- `workflow_versions`
  - `workflow_id`, `institution_id`, `location_id`
  - integer version number
  - immutable workflow definition JSON
  - validation result JSON
  - content class: `transactional_care`, `recall`, `sales`, etc.
  - published by/at
  - checksum for audit/debugging

- `workflow_runs`
  - `institution_id`, `location_id`
  - `workflow_id`, `workflow_version_id`
  - `contact_id`
  - optional trigger object references: appointment id, recall eligibility id, inbound call id, CSV import row id
  - status: `pending`, `running`, `waiting`, `completed`, `failed`, `cancelled`, `suppressed`
  - current step id/path
  - goal/outcome
  - start/completion timestamps
  - idempotency key for enrollment dedupe

- `workflow_step_executions`
  - `workflow_run_id`, `workflow_version_id`
  - logical step id from definition
  - status: `pending`, `scheduled`, `dispatching`, `waiting`, `completed`, `failed`, `skipped`, `cancelled`
  - attempt count, max attempts
  - scheduled_at UTC, local scheduled time and timezone for diagnostics
  - result summary and outcome code
  - unique key on `(workflow_run_id, step_id, attempt_number)` where appropriate

- `workflow_timers`
  - `institution_id`, `location_id`
  - `workflow_run_id`, `step_execution_id`
  - due_at UTC
  - status: `pending`, `claimed`, `fired`, `cancelled`, `failed`
  - claim metadata for distributed workers
  - unique active timer key for a waiting step

- `workflow_events`
  - append-style event log for run lifecycle and audit/debug timeline
  - PHI-free event type/details by default; encrypted payload only if explicitly required

- `workflow_enrollment_locks`
  - optional helper for deduping appointment/recall/contact enrollment across concurrent triggers

Every new tenant-scoped table must include RLS policies, app/admin role grants, indexes by `institution_id`, `location_id`, status, and due time where needed.

### Workflow Definition Schema

Define a strict JSON schema for saved workflow definitions:

- trigger block
- nodes/steps with stable ids
- edges and branch predicates
- action configs
- wait configs
- retry/max-attempt configs
- quiet-hours policy references
- merge-field/template references
- content class and compliance metadata

Definitions must be immutable once published. Drafts can be edited; published versions cannot.

### Services

- `WorkflowDefinitionService`
  - create/update draft workflows
  - clone from templates
  - publish immutable versions
  - pause/resume/archive workflows
  - **emergency halt of a published version** — terminate **all in-flight runs** on that version
    (cancel pending timers and queued channel attempts), distinct from pause. Pause only stops
    *new* enrollments; if a version is later found non-compliant (missing consent path, unlawful
    content), in-flight runs must be stoppable mid-flight (Gap Analysis Finding 9). Invoked by the
    Part 12 compliance layer and surfaced in operator tooling (Part 8).

- `WorkflowValidationService`
  - validates graph structure, reachable nodes, terminal exits, missing configs
  - validates merge fields by trigger/action type
  - validates quiet-hours/send-window rules before publish
  - **content-class + consent + PHI rules are supplied by Part 12's `ContentComplianceValidator`**
    (promotional-language detection in exempt-care/recall campaigns, PHI-term detection in
    message bodies, "no send step without a consent path"). This service invokes those rules; it
    does not define compliance policy locally.
  - **blast-radius check at publish** — enrollment ceiling / projected-spend warning via Part 12
    `BlastRadiusService` (Finding 10).

- `WorkflowEnrollmentService`
  - enrolls contacts from triggers, manual actions, CSV import, callback requests, appointment projections, or recall eligibility
  - dedupes conflicting active runs
  - applies eligibility gates before creating runs, including the Part 12 **frequency cap**
    (≤1/day, ≤3/week calls+texts per patient/provider — a v1 launch control, Finding 3) and
    enrollment-ceiling/spend caps for bulk enrollment

- `WorkflowRuntimeService`
  - interprets workflow definitions
  - dispatches steps
  - records step executions and events
  - resolves branches and terminal outcomes

- `WorkflowSchedulerService`
  - durable timer table poller
  - claims due timers with `FOR UPDATE SKIP LOCKED` or equivalent
  - handles retry/backoff and stale-claim recovery
  - computes local-time waits in `InstitutionLocation.timezone`

- `WorkflowActionRegistry`
  - maps action types to handlers: voice call, SMS, email, PMS recheck, PMS write-back, notify staff, update contact/tag, exit

- `WorkflowTriggerRegistry`
  - maps trigger types to enrollment sources: appointment time-offset, recall scan, manual, bulk/CSV, callback requested, inbound lead when later enabled

- `QuietHoursService`
  - shared policy evaluator for all outbound channels

## End-To-End Implementation Approach

1. Add workflow definition, version, run, step execution, timer, and event tables with RLS.
2. Define the workflow JSON schema and Pydantic models.
3. Implement draft CRUD and publish/version lifecycle.
4. Implement validation before publish.
5. Implement enrollment service with idempotent run creation.
6. Implement scheduler poller using durable timer rows and distributed claiming.
7. Implement runtime interpreter for a minimal set of primitives: trigger, wait, condition, action, exit.
8. Add action registry adapters for already planned voice, SMS, email, PMS recheck/write-back, and staff notification.
9. Add trigger providers for appointment working set, recall eligibility, callbacks, manual, and CSV.
10. Emit workflow events and SSE hints for progress UI.
11. Add operator dead-letter/replay integration for failed step dispatch.
12. Add metrics for due timers, stale timers, step failures, retries, and active runs.

## Architecture Decisions

- Build a database-backed durable scheduler instead of relying on Celery `eta`/`countdown`. Campaign waits can span days or weeks and must survive deploys, worker restarts, and broker churn.
- Store definitions as immutable versioned JSON. In-flight runs must continue on the version they enrolled under.
- Keep action execution modular through a registry. This prevents campaign-specific branching from spreading across services.
- Use location timezone as the authoritative v1 scheduling timezone, matching the scope and existing `InstitutionLocation.timezone`.
- Record workflow events separately from operational attempt logs. The event stream gives a readable run timeline without duplicating channel-specific audit records.
- Enforce compliance at runtime and publish time. Builder validation improves UX, but server-side validation and dispatch gates are authoritative.

## Technical Considerations

- Compute future local times using timezone names and `zoneinfo`, not fixed UTC offsets, to handle DST.
- Add jitter/smoothing for common local send times such as 9 AM to avoid vendor stampedes.
  This is more than jitter: the scheduler must **pace dispatch against the shared NexHealth
  per-key budget (~1,000/min) AND Retell per-workspace concurrency AND Twilio limits** at the
  burst moment (Gap 8 Problem B). Because Confirmation/Reminder **re-validate live at send time**
  (Finding 13), an 800-patient 9 AM batch = ~800 NexHealth calls in that minute — so validation
  must run *inside* a paced send loop (queue against the per-key budget, back off on 429, trust a
  recent-webhook freshness window to skip redundant re-validation), not all upfront. Coordinate
  with Part 9 (which paces backfill/reconciliation) so send-time and background PMS traffic share
  one budget view.
- Timer claiming must be safe across multiple API/worker tasks.
- A step can be due, then become invalid because consent changed or appointment state changed. Recheck gates at dispatch time.
- Workflow state JSON must not become a PHI dumping ground. Store references and minimal outcome codes; encrypt any unavoidable PHI.
- RLS context must be set for workflow workers just like existing Celery tasks.
- Publish should fail closed if validation is incomplete or any required channel readiness is missing.
- Workflow cancellation must cancel pending timers and future channel attempts.

## Dependencies

- Integration/data layer for appointment and recall triggers (Part 9).
- Outbound voice, SMS, and email action services (Parts 3/4/5).
- Per-tenant messaging provisioning/readiness (Part 10).
- **Part 12 compliance/consent layer** (multi-channel consent schema, `ComplianceGateService`,
  content validator, frequency cap, emergency halt, blast-radius/spend caps).
- Campaign builder and management UI (Parts 2/8).
- Operations tooling for failed runs and dead-letter replay (Part 8).
- Usage metering (Part 11) for spend-aware caps.

## Edge Cases

- A run is waiting when the workflow is paused.
- A workflow is edited while runs are active.
- Timer is claimed by a worker that crashes before dispatch.
- Duplicate trigger events try to enroll the same contact/appointment.
- Patient opts out after enrollment but before the next step.
- Appointment is cancelled after enrollment.
- Location timezone changes while runs are waiting.
- DST transition creates nonexistent or repeated local times.
- Channel action succeeds but workflow event write fails.
- Branch condition references data unavailable for the trigger payload.

## Risks

- The workflow runtime can become overly broad if arbitrary features are added before the four campaign paths are stable.
- Incorrect idempotency can double-contact patients.
- Poor timer design can miss or duplicate delayed work after deploys.
- Storing too much run context can increase PHI exposure and retention complexity.
- Validator gaps can allow non-compliant workflows to publish.

## Validation Strategy

- Unit tests for workflow schema validation and graph reachability.
- Unit tests for timezone scheduling, including DST boundaries.
- Unit tests for timer claiming and stale-claim recovery.
- Unit tests for enrollment dedupe and conflicting active runs.
- Integration tests for RLS on all workflow tables.
- Integration tests for publish immutability and in-flight version pinning.
- Integration tests for a minimal workflow: enroll -> wait -> SMS/action stub -> branch -> exit.
- Failure tests for worker crash after timer claim.
- Tests proving consent/appointment rechecks happen at dispatch time.

## Deployment Considerations

- Ship database tables and backend services before enabling UI publish.
- Run scheduler workers behind a feature flag and begin with action stubs in staging.
- Add a dedicated Celery queue or worker process for workflow dispatch if regular notification queues become noisy.
- Add CloudWatch metrics/alarms for stale timers, due timer backlog, repeated step failures, and dead-letter growth.
- Roll out one campaign template and one pilot location before enabling all templates.
- Document operator runbooks for pausing workflows, replaying failed steps, and cancelling stuck runs.

## Future Extensibility

- New trigger/action plugins without schema rewrites.
- Patient-level timezone support.
- A/B split and optimization primitives.
- External HTTP/webhook actions.
- DSO-level workflow templates inherited by child institutions.
- Temporal or managed workflow engine migration if table-backed runtime reaches scale limits.
