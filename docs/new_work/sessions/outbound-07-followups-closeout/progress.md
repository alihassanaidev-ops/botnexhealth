# Progress — Follow-ups & Bugs Closeout

## Session — 2026-07-04

### Resolved
- **XC-6 / CB-3 (consent capture — the Plan-07 blocker):**
  - `SmsComplianceService.record_consent` + `record_consent_identity` are now **channel-parameterized**
    (`channel: ConsentChannel|str = SMS`, backward-compatible — `suppress`/`release_suppression` unchanged).
  - Added `has_consent_record(institution_id, phone, channel)`.
  - `_trigger_callback_async` now **records a granted VOICE consent** (`source=system`,
    `reason="inbound_callback_request"`) for the contact's phone **only if none exists** (respects a prior
    opt-out; no row-spam). This lets AI-callback voice sends pass the gate → **Plan 07 is now functional
    end-to-end.** (Legal-review note in code: treats the inbound callback request as express voice consent.)
- **CB-2 (double-contact + quiet-hours):** `_trigger_callback_async` now **skips** if the source `Call` is
  missing or already `callback_resolved` (staff handled it). Quiet-hours defer-and-resume documented; the
  dev's `outbound-07-ai-callback/findings.md` D2/D4 note reconciled.
- **PR-1 (Plan 10 bug):** provisioning credential changes (`admin_institutions` PATCH + DELETE) now
  `log_audit(INSTITUTION_UPDATE)` with the actor + masked metadata (never the token/SID).
- **XC-1b (email crash-window):** `EmailNodeExecutor` sends a stable `Idempotency-Key: email:{run}:{node}`
  header to Resend, so a crash-retry is vendor-deduped. (SMS/voice provider keys = documented follow-up.)

### Tests
- New: callback resolved-skip (unit); email idempotency-header assertion (unit); voice-consent channel-scoped
  capture (real-Postgres integration). Updated the callback test session mock for the new Call/Contact loads.
- **1341 unit + 8 integration pass, 0 failures.** No migration needed (channel column + consent tables already exist).

### Explicitly deferred (documented in the register)
- Plan 03's own items (V-1 outcome loop, V-3 consent-basis hard-block, V-4 data model, V-6 retry) → the NEXT task.
- Commercial email consent *capture UI/intake* — transactional/care email now sends on implied consent when the
  email identifier is on file. Marketing/recall email remains blocked until an express-consent intake records it.
- Plan 06 C-1 dead confirm-branch (needs response capture / PMS write-back). CB-4 packaged template/tables.
- XC-1b SMS/voice provider idempotency keys (Twilio/Retell support varies).

**Nothing committed yet.**
