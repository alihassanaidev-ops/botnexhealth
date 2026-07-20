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

## Session: PMS capability gating slice

- **Status:** PMS capability gating slice complete; broader Plan 09 remains in progress
- Actions taken:
  - Added `PmsCapabilityService` that reads the checked-in NexHealth supported-API-per-PMS matrices.
  - Resolves a clinic's PMS identity from latest `nexhealth_sync_statuses` data.
  - Maps campaign/runtime requirements such as `patient_recalls`, `treatment_plans`, `procedures`, and `confirmation_writeback` to NexHealth API capabilities.
  - Adds per-location capability evaluation to `GET /automation/templates` and `GET /automation/templates/{id}` when `location_id` is provided.
  - Blocks template instantiation for unsupported/partial/unknown PMS capabilities.
  - Shows PMS feature support in the launch checklist for recall and treatment campaigns.
  - Gates appointment confirmation writeback before calling NexHealth if the selected PMS cannot edit appointments.
  - Updates the template picker to request location-aware template metadata, show `PMS ready`/`Unsupported`, and disable unsupported templates.

## Test Results: PMS capability gating slice

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pytest | `tests/unit/test_pms_capability_service.py tests/unit/test_automation_campaign_templates.py tests/unit/test_campaign_launch_checklist_service.py` | PMS capability evaluation, template gating, and checklist blockers pass | 38 passed, 3 existing warnings | passed |
| Ruff | touched backend service/route/task/test files | no lint issues | all checks passed | passed |
| Frontend vitest | `src/test/workflow-api.test.ts src/test/WorkflowTemplates.test.tsx` | API query params and unsupported template UI pass | 22 passed | passed |
| Frontend eslint | touched frontend API/page/test files | no lint issues | passed | passed |

## Session: webhook durability/ops hardening slice

- **Status:** webhook durability/ops hardening slice complete; Plan 09 complete
- Actions taken:
  - Added encrypted raw/redacted payload storage to `nexhealth_webhook_events`.
  - Added `source_event_id`, `payload_hash`, raw payload retain-until, and purge timestamp fields to improve debugability and dedupe diagnostics.
  - Added 14-day raw NexHealth webhook envelope retention plus retention purge wiring.
  - Updated appointment, patient, and sync-status webhook processors to store the raw envelope when claiming events.
  - Added hash fallback dedupe basis for patient/sync-status events that arrive without timestamps.
  - Added dead-letter capture for processor failures after a valid event is claimed, while acknowledging the webhook to avoid NexHealth endpoint deactivation from repeated 500s.
  - Strengthened subscription health checks so active subscriptions with no received events are marked failed after the stale window.

## Test Results: webhook durability/ops hardening slice

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Focused pytest | `tests/unit/test_nexhealth_projection.py tests/unit/test_nexhealth_appointment_webhook.py tests/unit/test_nexhealth_subscription_lifecycle.py tests/unit/test_retention_policy.py` | Webhook raw retention, DLQ, subscription monitoring, and retention purge behavior pass | 52 passed, 1 existing warning | passed |
| Ruff | touched backend model/route/service/migration/test files | no lint issues | all checks passed | passed |
| Alembic heads | `APP_ENV=local uv run alembic heads` | one current head | `20260720_nexhealth_webhook_durability (head)` | passed |
