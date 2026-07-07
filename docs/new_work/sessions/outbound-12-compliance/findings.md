# Findings: Outbound 12 — Compliance & Consent Gate

## Existing infrastructure (reusable)

## 2026-07-08 closeout update
- Product/legal resolved email/voice consent as **implied consent for transactional/care content** when the
  channel identifier is already on file.
- Marketing/recall/commercial email and voice still require express recorded consent.
- Revoked consent and DNC are checked before implied consent.
- Staff/admin DNC route now exists and is audited.
- Commercial express-consent capture remains deferred with the client-deferred lead-intake pipeline.

The original recommendation below for explicit email/voice consent is superseded for transactional/care content;
it still applies to commercial/recall content.

### Consent / suppression layer
- `src/app/models/sms_consent.py` — `ConsentRecord`, `SmsSuppression`, `DoNotContact`
- `ConsentChannel` enum currently only has `SMS = "sms"` — needs EMAIL + VOICE for future channels
- `SmsComplianceService.assert_can_send()` (L66) handles SMS suppression + DNC check; can be called directly from the gate for the SMS path

### Quiet hours
- `InstitutionLocation.timezone: str` (default "UTC") — field exists at L64
- `LocationOperatingHours` — one row per day-of-week, `is_open`, `open_time`, `close_time`
- `AutomationWorkflowRun.location_id` — run knows which location enrolled the contact

### Gate contract (frozen)
- `ComplianceGate` Protocol at `compliance_gate.py:28` — `async check(run, channel_type) → GateResult`
- `GateResult(action: Literal["allow","block","hold"], reason: str | None)`
- `channel_type` values: `"send_sms"`, `"send_voice"`, `"send_email"`
- `NoOpComplianceGate` at L42 — stub, always allows

### Dispatcher hook
- `step_dispatcher.py:115` — gate checked before every send node
- On `block`/`hold`: dispatcher calls `runtime.complete_step(step, result_code="compliance_blocked")`
  (current stub — will need to route to `runtime.block_run()` instead)

---

## Decision rationale

### D1 — Emergency halt: column vs. table
**Recommendation: separate `OutboundEmergencyHalt` table.**
- Rest of codebase uses explicit audit tables (AuditLog, DeadLetterQueue)
- A column on Institution has no audit trail: who halted, when, why, who released
- Table allows: activate/release timestamps, reason text, acting user FK, future per-workflow-type override
- Simple implementation: gate queries `WHERE institution_id=X AND released_at IS NULL LIMIT 1`

### D2 — Email/voice consent (original recommendation; superseded for transactional/care)
**Original recommendation: require explicit consent per channel.**
- CASL (Canada's Anti-Spam Legislation) requires express consent for commercial electronic messages, including transactional email to new contacts
- Existing `ConsentRecord.channel` already supports multi-channel by design (string field, not hard-typed)
- For v1: add EMAIL + VOICE to `ConsentChannel` enum; gate returns `block` if no active `ConsentRecord` for channel
- Implication: clinic must collect email/voice consent separately from SMS consent. Product must decide how/when that happens (intake form? in-app toggle?)
- **If Product decides implicit consent is OK** (patient is an existing patient = implied consent): gate simply skips the consent check for email/voice and we can add it later. This is the simpler v1 path but carries legal risk.

### D3 — Quiet hours source
**Recommendation: reuse `LocationOperatingHours` for v1.**
- Clinic operating hours are already configured per-location
- Sending outside clinic hours is operationally problematic anyway (no staff to handle replies)
- If `location_id` is NULL on the run (edge case), fall back to UTC 8am–8pm window
- If location has no `LocationOperatingHours` rows, skip the quiet hours check (unconfigured = no restriction, consistent with how slot filtering works)
- Future: add a separate `OutboundQuietHoursConfig` table if clinics want different send windows

### D4 — Hold semantics
**Recommendation: terminate with `compliance_hold` outcome for v1.**
- The compliance_gate.py docstring already states: "hold terminates the run with outcome 'compliance_hold' rather than re-queuing"
- Re-queue support (e.g., retry when quiet hours window opens) is deferred — requires timer re-scheduling logic
- This is already the agreed pattern from Plan 01; gate just needs to map "hold" → block_run()

---

## Open risks
- `ConsentRecord` uses `phone_hash` for lookup. The `AutomationWorkflowRun` only has `contact_id`, not the phone directly. Gate will need to load the `Contact` to get the phone, then hash it for the consent lookup. `SmsComplianceService.identify(phone)` does the hashing.
- If `run.contact_id` is NULL (bulk trigger without a specific contact), the consent check must be skipped or fail-safe (block). Clarify with CTO.
