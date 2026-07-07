# Task Plan: Outbound 12 — Compliance & Consent Gate

## Goal
Replace `NoOpComplianceGate` with a real `ComplianceGateService` that blocks/holds
workflow runs before any send node fires. Three sequential checks: emergency halt →
quiet hours → channel consent.

## Current Status
**Complete for agreed scope** ✅ — gate + halt + quiet hours + DNC + consent basis shipped. Transactional/care
email and voice now use implied consent when the identifier is on file; commercial/recall still require express
consent.

## Prerequisites (confirmed present)
- `ComplianceGate` protocol + `GateResult` already frozen in `compliance_gate.py`
- `ConsentRecord` / `SmsSuppression` / `DoNotContact` models in `sms_consent.py`
- `SmsComplianceService.assert_can_send()` handles SMS suppression check
- `InstitutionLocation.timezone` field exists
- `LocationOperatingHours` (open/close per day-of-week) exists
- `AutomationWorkflowRun` carries `institution_id`, `location_id`, `contact_id`

## Decisions Needed Before Code (flag for CTO/Product)
See `findings.md` for full rationale on each.

| # | Decision | Recommended | Owner | Status |
|---|----------|-------------|-------|--------|
| D1 | Emergency halt: column on Institution vs. separate audit table | Separate `OutboundEmergencyHalt` table | CTO | ✅ **Resolved: separate table** |
| D2 | Consent for email/voice: explicit opt-in required or implicit relationship? | Implied for transactional/care, express for marketing/recall | CTO + Legal | ✅ **Resolved: Option B for transactional; express still required for commercial** |
| D3 | Quiet hours source: reuse `LocationOperatingHours` or separate outbound window? | Reuse operating hours for v1 | Product | ✅ **Resolved: reuse LocationOperatingHours; separate config deferred** |
| D4 | Hold semantics: terminate with `compliance_hold` outcome or re-queue? | Terminate for v1 (re-queue deferred) | CTO | ✅ **Resolved: terminate with compliance_hold; re-queue deferred** |
| D5 | NULL contact_id on run: skip consent check or fail-safe block? | Fail-safe block | CTO | ✅ **Resolved: block — no contact = no consent can be verified** |

## Planned Slices

### Slice 1 — Emergency Halt Model + Migration
- [ ] Add `OutboundEmergencyHalt` table (institution_id, halted_by_user_id, reason, created_at, released_at, released_by_user_id)
- [ ] Migration script
- [ ] Expose in `models/__init__.py`
- **Blocked on D1**

### Slice 2 — ConsentChannel Expansion
- [ ] Add `EMAIL = "email"` and `VOICE = "voice"` to `ConsentChannel` enum in `sms_consent.py`
- [ ] Update `CheckConstraint` in `ConsentRecord` and `SmsSuppression`
- [ ] Migration for constraint update
- **Blocked on D2** (if email/voice don't need consent records, skip this)

### Slice 3 — ComplianceGateService
- [ ] New file: `src/app/services/automation/compliance_gate_service.py`
- [ ] Implements `ComplianceGate` protocol
- [ ] Check 1: emergency halt (query `OutboundEmergencyHalt`)
- [ ] Check 2: quiet hours (load location operating hours + timezone, check wall-clock)
- [ ] Check 3: channel consent (delegate to `SmsComplianceService` for SMS; stub allow for email/voice until D2 resolved)
- **Blocked on D1, D3, D4**

### Slice 4 — Wire into Dispatcher
- [ ] Replace `NoOpComplianceGate` with `ComplianceGateService` in `step_dispatcher.py`
- [ ] Pass DB session to gate constructor
- [ ] Map `GateResult.action="block"/"hold"` → `runtime.block_run()` with `blocked_reason`

### Slice 5 — Admin API (Emergency Halt Controls)
- [ ] `POST /institution-admin/outbound/halt` — activate halt
- [ ] `DELETE /institution-admin/outbound/halt` — release halt
- [ ] `GET /institution-admin/outbound/halt` — current halt status
- [ ] `INSTITUTION_ADMIN` role required, audit logged

### Slice 6 — Tests
- [ ] Gate blocks on active emergency halt → run outcome `compliance_hold`
- [ ] Gate blocks on quiet hours (mocked current time outside window)
- [ ] Gate passes outside quiet hours
- [ ] Gate blocks on SMS suppression (opt-out)
- [ ] Gate passes when consent granted + no halt + in hours
- [ ] Emergency halt API: activate / release / status

### Slice 7 — DNC + Implied Transactional Consent Closeout
- [x] Add staff/admin do-not-contact route
- [x] Audit DNC set/release actions
- [x] Gate DNC before channel consent
- [x] Allow transactional/care email + voice by implied consent when identifier is on file
- [x] Keep marketing/recall email + voice blocked unless express consent exists

## Files Touched
| File | Change |
|------|--------|
| `src/app/models/sms_consent.py` | Add EMAIL/VOICE to ConsentChannel + update constraints |
| `src/app/models/outbound_halt.py` | New — OutboundEmergencyHalt model |
| `src/app/models/__init__.py` | Export OutboundEmergencyHalt |
| `src/app/services/automation/compliance_gate_service.py` | New — real gate |
| `src/app/services/automation/step_dispatcher.py` | Swap NoOp for real gate |
| `src/app/api/routes/automation_workflows.py` | Emergency halt endpoints |
| `src/app/api/routes/do_not_contact.py` | DNC admin endpoints |
| `alembic/versions/` | 2 migrations (halt table + consent constraint) |
| `tests/unit/test_automation_compliance_gate_service.py` | New test file |
| `tests/unit/test_do_not_contact.py` | New test file |
