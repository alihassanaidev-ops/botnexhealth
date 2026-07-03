# Progress: Outbound 12 — Compliance & Consent Gate

## Initial State
- **Status:** Planning — awaiting CTO/Product decisions on D1–D4 before code starts
- `NoOpComplianceGate` in place; step dispatcher fully stubbed
- All prerequisite models and services exist

## Decisions — all resolved (2026-07-03)
D1 separate OutboundEmergencyHalt table · D2 explicit consent per channel · D3 reuse LocationOperatingHours · D4 terminate compliance_hold · D5 block on NULL contact_id

## Slices

### Slice 1 — Emergency halt model + migration
- **Status:** complete ✅
- `src/app/models/outbound_halt.py` — OutboundEmergencyHalt model
- `src/app/models/__init__.py` — export added
- `alembic/versions/20260703_outbound_halt.py` — table + partial index + RLS + grant
- `alembic/versions/20260510_consolidated_baseline.py` — added to PROTECTED_TABLES, _outbound_halt_expr(), policy spec
- RLS: super_admin OR (celery/dead_letter + institution match) OR (user + INSTITUTION_ADMIN + institution match)
- Grant: SELECT, INSERT, UPDATE to nexhealth_app (no DELETE — append-only for audit)
- Tests: 5/5 static RLS coverage tests passing
- Migration: applied and verified in local Docker DB
### Slice 2 — ConsentChannel expansion
- **Status:** complete ✅
- `src/app/models/sms_consent.py` — added EMAIL + VOICE to ConsentChannel enum; updated CheckConstraints on ConsentRecord and SmsSuppression
- `alembic/versions/20260703_consent_channel.py` — drops/recreates ck_consent_records_channel and ck_sms_suppressions_channel on existing DBs
- Fresh DBs covered automatically via create_all() from updated model
- Migration applied and verified in local Docker DB
- Tests: 3/3 static constraint coverage tests passing (`test_consent_channel_coverage.py`)
### Slice 3 — ComplianceGateService
- **Status:** complete ✅
- `src/app/services/automation/compliance_gate_service.py` — 3-check gate: halt → quiet hours → consent
- SMS delegates to SmsComplianceService.assert_can_send(); email/voice check ConsentRecord directly
- `now` injectable for testability; quiet hours skip when no location or no rows configured

### Slice 4 — Wire into dispatcher
- **Status:** complete ✅
- `src/app/tasks/automation_workflow.py` — both WorkflowStepDispatcher instantiation sites now pass `gate=ComplianceGateService(session)`

### Slice 5 — Admin halt API
- **Status:** complete ✅
- `src/app/api/routes/automation_workflows.py` — added GET/POST/DELETE `/automation/workflows/outbound-halt`
- POST is idempotent (returns existing active halt if present)
- DELETE returns 404 if no active halt

### Slice 6 — Tests
- **Status:** complete ✅
- `tests/unit/test_automation_compliance_gate_service.py` — 13 tests covering all 3 check layers
- 23/23 gate tests passing; 206/206 full automation suite passing — no regressions
