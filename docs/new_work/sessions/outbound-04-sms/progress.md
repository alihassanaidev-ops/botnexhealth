# Progress: Outbound 04 — Outbound SMS

## Initial State
- `_dispatch_send_stub` handles all send nodes with `result_code="stub_dispatched"`
- ComplianceGateService already wired (Plan 12)
- SmsService + per-institution Twilio creds already in place (Plan 10)

## Slices

### Slice 1 — Template renderer
- **Status:** complete ✅
- `src/app/services/automation/template_renderer.py` — regex `{{var}}` substitution, unknown vars → blank

### Slice 2 — SmsNodeExecutor
- **Status:** complete ✅
- `src/app/services/automation/sms_node_executor.py` — loads contact + location, renders template, calls SmsService, fail-safe on every error path

### Slice 3 — Wire dispatcher
- **Status:** complete ✅
- `step_dispatcher.py` — `SendSmsNode` routes to `SmsNodeExecutor`; Voice/Email still on stub

### Slice 4 — Tests
- **Status:** complete ✅
- `tests/unit/test_outbound_sms_executor.py` — 13 tests (7 renderer + 6 executor)
- `tests/unit/test_automation_compliance_gate.py` — patched executor in allow-gate test (gate tests cover gate logic, not send execution)
- 1178/1178 unit tests passing, no regressions
