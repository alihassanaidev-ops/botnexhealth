# Progress Log

## Session: GoTracker Synchronizer PMS adapter

- **Status:** adapter/config/UI/webhook receiver and subscription automation slice complete
- Actions taken:
  - Added location-level GoTracker configuration fields and migration.
  - Added encrypted product-key storage helpers on `InstitutionLocation`.
  - Added `gotracker` as a supported PMS type in backend tenant APIs and frontend tenant creation.
  - Added `GoTrackerClient`, response mappers, and `GoTrackerAdapter`.
  - Routed `pms_type=gotracker` through the PMS factory.
  - Added GoTracker setup fields to the location admin form and PMS readiness indicators to admin dashboards.
  - Added a signed location-scoped GoTracker webhook receiver at `/api/v1/gotracker/webhooks/{location_id}`.
  - Added durable GoTracker webhook event ledger storage with short-term raw payload retention.
  - Mapped GoTracker patient events into contact/patient projection refresh.
  - Mapped GoTracker appointment events into appointment projection refresh, workflow triggers, and cancellation handling.
  - Added GoTracker webhook subscription lifecycle rows and worker auto-create task.
  - Auto-create posts configured GoTracker locations to `POST /api/webhooks/subscriptions` with a location-scoped callback URL, one documented `event_types` value per request, and the global webhook signing secret.
  - Incoming GoTracker webhooks now update subscription health/last-event state.
  - Added focused backend tests for adapter mapping, client envelope handling, documented endpoints, booking, cancellation, and factory routing.
  - Added focused webhook tests for signature verification, appointment trigger, appointment cancellation, patient refresh, and duplicate handling.
  - Added focused subscription lifecycle tests for local row creation, remote POST body/path, event health marking, stale health failure, callback URL construction, and provider ID extraction.

## Verification

| Test | Input | Expected | Actual | Status |
|------|-------|----------|--------|--------|
| Compile smoke | `python -m compileall -q src/app/pms/gotracker src/app/pms/factory.py src/app/models/institution_location.py src/app/api/routes/admin_institutions.py src/app/api/models.py` | touched backend modules compile | no output, exit 0 | passed |
| Focused pytest | `UV_CACHE_DIR=/tmp/uv-cache APP_ENV=test uv run pytest tests/unit/test_gotracker_adapter.py tests/unit/test_pms_factory.py tests/unit/test_sync_service.py tests/unit/test_institution_invite_role_validation.py` | GoTracker adapter/factory and nearby sync tests pass | 35 passed, 5 existing warnings | passed |
| Webhook/RBAC pytest | `UV_CACHE_DIR=/tmp/uv-cache APP_ENV=test uv run pytest tests/unit/test_gotracker_webhooks.py tests/unit/test_gotracker_adapter.py tests/unit/test_pms_factory.py tests/unit/test_rbac_route_matrix.py` | GoTracker webhook receiver and signed route boundary pass | 542 passed, 1 existing warning | passed |
| Subscription/webhook pytest | `UV_CACHE_DIR=/tmp/uv-cache APP_ENV=test uv run pytest tests/unit/test_gotracker_subscription_lifecycle.py tests/unit/test_gotracker_webhooks.py` | GoTracker subscription lifecycle and webhook processing pass | 15 passed, 1 existing warning | passed |
| GoTracker touched backend pytest | `UV_CACHE_DIR=/tmp/uv-cache APP_ENV=test uv run pytest tests/unit/test_gotracker_subscription_lifecycle.py tests/unit/test_gotracker_webhooks.py tests/unit/test_gotracker_adapter.py tests/unit/test_pms_factory.py tests/unit/test_rbac_route_matrix.py tests/unit/test_retention_policy.py` | GoTracker adapter/webhook/subscription/RBAC/retention slice passes | 564 passed, 1 existing warning | passed |
| Touched backend ruff | `UV_CACHE_DIR=/tmp/uv-cache uv run ruff check src/app/models/gotracker_webhook_subscription.py src/app/services/automation/gotracker_subscription_service.py src/app/api/routes/gotracker_webhooks.py src/app/tasks/automation_workflow.py src/app/worker.py tests/unit/test_gotracker_subscription_lifecycle.py tests/unit/test_gotracker_webhooks.py` | touched backend files pass lint | passed | passed |
| Frontend build | `npm run build` in `nexus-dashboard-web` | tenant UI typechecks and production build completes | build passed with existing Browserslist/chunk-size warnings | passed |
| Live local Synchronizer subscription QA | local Docker API/worker + ngrok + test-only Synchronizer location | subscription lifecycle creates event-scoped remote subscriptions | `POST /api/webhooks/subscriptions` returned five `201 Created`; remote list showed active subscriptions for appointment.created, appointment.updated, appointment.cancelled, patient.created, patient.updated | passed |
| Live local webhook receive QA | signed `patient.created` and `appointment.created` payloads posted through ngrok to local API | API verifies signature, stores events, updates projections, queues appointment workflow trigger | both returned 200; `gotracker_webhook_events` rows completed; patient/appointment working-set rows created | passed |

## Remaining Decisions

- Exact real GoTracker webhook payload examples from staging.
- Real PMS-agent-originated GoTracker events still need staging confirmation; local QA proved subscription creation and public callback receipt with signed test payloads.
