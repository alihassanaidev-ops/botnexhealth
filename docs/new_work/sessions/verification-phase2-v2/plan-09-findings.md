# Plan 09 - Integration & Data Layer - Verification Findings

Audited: 2026-07-03. Updated: 2026-07-08 after Plan 09 resilience commits and focused local verification.

## Summary

Plan 09 is **code-complete for the core local/data-layer scope** and remains **staging-pending** for the live
NexHealth API pieces.

The previously missing core pieces now exist:
- `appointment_working_set` projection.
- `nexhealth_webhook_events` ledger for event-level idempotency.
- cancellation/reschedule handling.
- time-aware appointment idempotency keys so reschedules re-enroll at the new time.
- `PmsLiveRevalidationService` before sends.
- subscription lifecycle table/service.
- initial backfill service.
- reconciliation sweep.

Focused local verification on 2026-07-08 passed:

```bash
APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run pytest \
  tests/unit/test_nexhealth_projection.py \
  tests/unit/test_nexhealth_appointment_webhook.py \
  tests/unit/test_nexhealth_subscription_lifecycle.py \
  tests/unit/test_nexhealth_backfill_reconciliation.py \
  tests/unit/test_pms_revalidation.py \
  tests/unit/test_nexhealth_adapter_appointments.py \
  -q
```

Result: **57 passed**, 2 warnings.

## What Is Built

### Appointment Projection
- `src/app/models/appointment_working_set.py`
- `alembic/versions/20260707_appointment_working_set.py`
- `src/app/services/automation/nexhealth_projection_service.py`

The webhook/backfill/reconciliation paths upsert last-seen appointment scheduling state. This enables reschedule
detection and gives revalidation a recent projection to trust before falling back to live NexHealth lookup.

### Event Ledger
- `src/app/models/nexhealth_webhook_event.py`
- `alembic/versions/20260707_appointment_working_set.py`

Webhook events are claimed by event/change identity before processing, so duplicate deliveries do not repeatedly
drive the same workflow side effects.

### Webhook Processing
- `src/app/api/routes/nexhealth_webhooks.py`

The route verifies signatures, resolves location/contact, records subscription activity, projects appointment state,
cancels runs for cancelled appointments, and re-enrolls rescheduled appointments using the new appointment start time.

### Revalidation
- `src/app/services/automation/revalidation.py`
- `src/app/services/automation/step_dispatcher.py`
- `src/app/tasks/automation_workflow.py`

Send-time revalidation now checks whether the appointment was cancelled or rescheduled before dispatching outbound
steps. Fresh projection rows avoid unnecessary live calls; stale/missing rows fall back to live NexHealth lookup.

### Subscription Lifecycle
- `src/app/models/nexhealth_webhook_subscription.py`
- `alembic/versions/20260708_nexhealth_webhook_subscriptions.py`
- `src/app/services/automation/nexhealth_subscription_service.py`

The service creates and tracks one expected appointment-webhook subscription per configured NexHealth location,
including provider id, status, last event time, health timestamps, and error metadata.

### Backfill And Reconciliation
- `src/app/services/automation/nexhealth_backfill_service.py`
- `src/app/tasks/automation_workflow.py`
- `src/app/worker.py`

Backfill and reconciliation pull appointments through the NexHealth adapter, project them, trigger new/rescheduled
appointment workflows, and cancel stale/cancelled runs.

## What Could Not Be Verified Locally

Live NexHealth staging verification was not run from this workspace because local config does not contain usable
staging credentials:
- `.env` still contains placeholder NexHealth values.
- `infra/config/staging.json` references the staging API key by AWS Secrets Manager ARN, not a local secret.
- Network access from this environment is restricted.

Remaining live verification:
- create/list/health-check appointment webhook subscriptions against a staging NexHealth tenant.
- confirm the exact provider subscription endpoint and response payload.
- confirm appointment webhook payload shapes for created, updated, cancelled, and rescheduled appointments.
- confirm backfill filters/pagination against real `list_appointments` responses.
- confirm reconciliation repairs stale/missing projection rows against real tenant data.

## Product Decisions

No product decision blocks Plan 09 verification.

The remaining question is operational/empirical: after staging verification, decide whether a dedicated
`recall_eligibility_working_set` is actually needed. Current recall pull can operate without that table, so this
should be decided from real NexHealth recall behavior and launch requirements rather than built automatically.

Tenant-owned NexHealth key routing remains optional/vendor-confirmed and is not required for current Plan 09
completion.

## Verdict

Plan 09 should stay at **~80% / staging-pending** until a live NexHealth tenant proves subscription creation,
backfill, reconciliation, and real webhook payload compatibility.

The remaining risk is not product scope. It is integration proof against NexHealth.
