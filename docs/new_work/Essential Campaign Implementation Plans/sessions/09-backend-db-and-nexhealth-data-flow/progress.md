# Progress Log

## Session: patient webhook support slice

- **Status:** patient webhook slice complete; broader Plan 09 remains in progress
- Actions taken:
  - Added `patient_working_set` model and migration.
  - Extended NexHealth webhook event ledger with `nexhealth_patient_id`.
  - Extended subscription defaults to include `patient_created` and `patient_updated`.
  - Added patient event handling on the existing NexHealth receiver URL plus a dedicated `/patients` route.
  - Patient webhooks refresh encrypted local `contacts`, grant contact-location access, and update the patient projection.
  - Patient webhooks do not trigger campaign enrollment directly.

## Test Results

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pytest | `tests/unit/test_nexhealth_appointment_webhook.py tests/unit/test_nexhealth_projection.py tests/unit/test_nexhealth_subscription_lifecycle.py` | Patient + appointment webhook behavior passes | 32 passed, 1 warning | passed |
| Ruff | touched backend route/service/model/test files | no lint issues | all checks passed | passed |

## Session: sync-status support slice

- **Status:** sync-status slice complete; broader Plan 09 remains in progress
- Actions taken:
  - Added `nexhealth_sync_statuses` model and migration.
  - Extended NexHealth subscription defaults/resource mapping to include `sync_status_read_change` and `sync_status_write_change` as `SyncStatus`.
  - Added sync-status webhook ingestion on the existing `/nexhealth/webhooks/appointments` receiver plus a dedicated `/sync-status` route.
  - Added `NexHealthSyncStatusService` for webhook upsert, payload normalization, and scheduled `GET /sync_status` polling.
  - Added a 15-minute Celery beat task for sync-status polling.
  - Added launch-checklist PMS read/write sync health item.
  - Added dispatch-time guard: if appointment projection is stale/missing and PMS read sync is known unhealthy, the send exits as `skipped_pms_read_unhealthy`.

## Test Results: sync-status slice

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pytest | `tests/unit/test_nexhealth_subscription_lifecycle.py tests/unit/test_nexhealth_sync_status_service.py tests/unit/test_nexhealth_appointment_webhook.py tests/unit/test_campaign_launch_checklist_service.py tests/unit/test_pms_revalidation.py tests/unit/test_rbac_route_matrix.py` | Sync subscriptions, webhook handling, checklist, revalidation, RBAC pass | 547 passed, 2 existing warnings | passed |
| Ruff | touched backend route/service/model/task/test files | no lint issues | all checks passed | passed |
| Alembic heads | `APP_ENV=local uv run alembic heads` | one current head | `20260718_nexhealth_sync_status (head)` | passed |

## Session: backfill/reconciliation jobs slice

- **Status:** backfill/reconciliation slice complete; broader Plan 09 remains in progress
- Actions taken:
  - Added patient-specific backfill/reconciliation watermarks to `nexhealth_webhook_subscriptions`.
  - Added raw `NexHealthAdapter.list_patients()` using `GET /patients`, pagination, location scope, and optional `updated_since`.
  - Added `NexHealthPatientSyncService` to refresh contacts and `patient_working_set` from REST pulls.
  - Added `backfill_nexhealth_patients` and `reconcile_nexhealth_patients` Celery tasks.
  - Scheduled patient reconciliation every 6 hours to repair missed patient webhooks.
  - Confirmed appointment backfill/reconciliation and basic recall polling already existed; durable recall working set stays with PMS capability gating.

## Test Results: backfill/reconciliation slice

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pytest | `tests/unit/test_nexhealth_backfill_reconciliation.py tests/unit/test_nexhealth_adapter_appointments.py tests/unit/test_nexhealth_projection.py tests/unit/test_nexhealth_subscription_lifecycle.py tests/unit/test_automation_plan09.py` | Appointment/patient sync, adapter pagination, projection, subscription, and recall scanner tests pass | 51 passed, 1 existing warning | passed |
| Ruff | touched backend migration/model/adapter/service/task/test files | no lint issues | all checks passed | passed |
| Alembic heads | `APP_ENV=local uv run alembic heads` | one current head | `20260719_patient_backfill_watermarks (head)` | passed |
