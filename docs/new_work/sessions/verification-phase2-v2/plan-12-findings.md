# Plan 12 — Compliance, Consent & Access-Control — Verification Findings

Audited: 2026-07-03. Updated: 2026-07-08 after Plan 12 closeout commit `63b0363`.

## Verdict

Plan 12 is **complete for the agreed compliance-gate scope**.

The original implementation was a thin gate slice. Subsequent closeout work resolved the functional blockers that
affected the shipped campaigns:
- The real gate is used on the shared dispatcher path, not bypassed by inline enrollment.
- Transactional/care email and voice are allowed by implied consent when the patient's channel identifier is on
  file.
- Marketing/recall still require express consent.
- Revoked consent and do-not-contact are checked before implied consent, so opt-outs remain authoritative.
- Do-not-contact admin API exists for staff/admin operations.
- Emergency halt now terminates in-flight runs/timers through the workflow halt services.

## Current Implemented State

### Compliance Gate
- `ComplianceGateService` checks emergency halt, quiet hours, do-not-contact, and channel consent/basis before send.
- SMS delegates to `SmsComplianceService`.
- Email uses email identity (`ConsentRecord.email_hash`) rather than phone-only consent.
- Voice uses phone identity.
- Transactional/care content can send on implied consent for email/voice when the identifier is already on file.
- Marketing/recall content requires express recorded consent.

### Do Not Contact
- `src/app/api/routes/do_not_contact.py`
  - Admin/staff route for adding and releasing do-not-contact suppressions.
  - Audit actions added for DNC changes.
- Gate honors DNC across channels.

### Emergency Halt
- `OutboundEmergencyHalt` remains append-only operational state.
- Institution-wide halt routes exist.
- Per-workflow emergency halt route exists.
- Halt actions terminate active/waiting runs through `AutomationWorkflowDefinitionService`.

### Content Basis
- Gate enforces the content-class/basis distinction:
  - care/transactional: implied allowed when identifier is on file, unless revoked/DNC.
  - recall/marketing: express consent required.

## Tests

- `tests/unit/test_automation_compliance_gate_service.py`
  - emergency halt, quiet hours, SMS, email, voice, revoked consent, implied transactional consent, and
    marketing/recall blocking cases.
- `tests/unit/test_do_not_contact.py`
  - DNC admin route behavior.
- `tests/unit/test_rbac_route_matrix.py`
  - route exposure/role matrix.

## Remaining / Deferred

- **Commercial consent capture:** deferred with the client-deferred lead-intake/commercial workflow. The gate
  correctly blocks marketing/recall email/voice until express consent exists.
- **Separate named `ConsentService` / `SuppressionService`:** not required now; behavior lives in the gate and
  channel-generic `SmsComplianceService` helpers.
- **US patient-local quiet hours:** clinic-timezone enforcement is the current policy. Patient-local quiet-hours are
  future policy work if required.
- **Frequency/spend/blast-radius caps:** dropped by product-owner no-caps decision.

## Completion Decision

Plan 12 is marked 100% because the required compliance behavior for current campaigns is implemented and enforced.
The unbuilt items are either product-dropped, deferred with commercial lead intake, or policy/architecture
hardening rather than blockers.
