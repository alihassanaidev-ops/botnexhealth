# Progress: Outbound 09 - Integration & Data Layer

## Slice 1 - Appointment Trigger
- **Status:** complete
- Added `AppointmentTriggerService`.
- Added appointment workflow discovery and enrollment ETA computation.
- Added appointment-trigger Celery task flow.

## Slice 2 - Recall Scanner
- **Status:** implemented without dedicated projection
- Pulls NexHealth recall records per configured location and enrolls due patients with stable recall idempotency keys.
- A dedicated `recall_eligibility_working_set` remains a post-staging decision, not a current blocker.

## Slice 3 - Bulk Enrollment
- **Status:** complete
- Added bulk-enroll API endpoint.
- Enqueues workflow enrollment tasks for up to 500 items.

## Slice 4 - NexHealth Appointment Webhook
- **Status:** complete for first pass
- Added webhook route, signature verification, event filtering, location/contact resolution, and task dispatch.

## 2026-07-08 Verification Update
- Plan 09 resilience core now exists in code: appointment projection, webhook event ledger, cancellation/reschedule
  handling, time-aware reschedule re-enroll, PMS live revalidation, subscription lifecycle, backfill, and
  reconciliation.
- Focused local verification passed:
  `APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/unit/test_nexhealth_projection.py tests/unit/test_nexhealth_appointment_webhook.py tests/unit/test_nexhealth_subscription_lifecycle.py tests/unit/test_nexhealth_backfill_reconciliation.py tests/unit/test_pms_revalidation.py tests/unit/test_nexhealth_adapter_appointments.py -q`
- Result: 57 passed, 2 warnings.
- Live NexHealth staging verification was not run from this workspace because `.env` contains placeholder NexHealth
  values, staging secrets are referenced by AWS Secrets Manager ARN, and this environment has restricted network
  access.
- Remaining Plan 09 work is verification against a real/staging NexHealth tenant, not a product decision.
