# Progress: Outbound 03 — Outbound Voice

## Initial State
- `_dispatch_send_stub` handles SendVoiceNode with result_code="stub_dispatched"
- SMS (Plan 04) and Email (Plan 05) executors live
- Webhook already persists outbound Call rows (direction=="outbound")
- ScaleNexusAI agent JSON in project root surveyed

## Slices

### Slice 1 — Schema: retell_from_number
- **Status:** complete ✅
- `InstitutionLocation.retell_from_number` (nullable String(20))
- `alembic/versions/20260703_retell_from_number.py` chains from `20260703_provisioning`
- Revision id 27 chars (< 32 limit). `alembic heads` → single head, chain verified.
- **Applied to DB ✅** — `alembic upgrade head` run; `retell_from_number character varying(20)` confirmed on institution_locations.

### Slice 2 — VoiceNodeExecutor
- **Status:** complete ✅
- `src/app/services/automation/voice_node_executor.py` — resolves contact.phone +
  location.retell_from_number, POSTs Retell v2/create-phone-call with override_agent_id,
  dynamic vars {first_name, user_number}, metadata {workflow_run_id}. Fail-safe on every path.
- Does NOT write the Call row — existing webhook records outbound calls.

### Slice 3 — Wire dispatcher
- **Status:** complete ✅
- `step_dispatcher.py` — `SendVoiceNode` routes to `VoiceNodeExecutor`. All 3 send channels now live.
- Module docstring updated; stub kept only as defensive fallback.

### Slice 4 — Tests
- **Status:** complete ✅
- `tests/unit/test_outbound_voice_executor.py` — 7 tests: no contact / not found / no phone /
  no from-number / Retell not configured / success (asserts payload + metadata) / HTTP error.
- Voice + dispatcher + compliance-gate + tenant-scope suites: 40 passed.
- Full unit suite: 1192 passed. Pre-existing unrelated failures only:
  - `test_locations_routes.py`, `test_nexhealth_client.py` — collection error (`respx` not installed)
  - `test_appointments_routes_coverage.py` (3) — Redis ConnectionRefused (infra down)

## Decisions captured (see task_plan.md)
- D1 Fire-and-forget v1 · D1a metadata.workflow_run_id correlation hedge · D2 new retell_from_number field · D3 node's retell_agent_id via override_agent_id.

## Test / migration commands used
- `APP_ENV=local uv run pytest tests/unit/test_outbound_voice_executor.py ... -q`
- `APP_ENV=local uv run alembic heads` (offline chain check)
- Migration to run when DB up: `DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5433/nexhealth APP_ENV=local uv run alembic upgrade head`
