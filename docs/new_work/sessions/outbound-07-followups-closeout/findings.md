# Findings — closeout

## XC-6 consent capture
- Only `ConsentRecord` writers: `sms_compliance.record_consent` (L332) + `record_consent_identity` (L372),
  both hardcode `channel=ConsentChannel.SMS.value`. Callers: `suppress` (L276) and `release_suppression`
  (L320), both SMS-context and pass no channel → adding `channel: ConsentChannel|str = SMS` default is
  backward-compatible.
- Gate voice path `_check_phone_consent(..., VOICE)` requires a granted `ConsentRecord`; none is ever written
  for voice → all voice sends blocked `no_voice_consent`.
- **Plan:** (a) add `channel` param to both writers; (b) add `has_consent_record(institution_id, phone, channel)`;
  (c) in `_trigger_callback_async`, record a **granted VOICE consent** (`source=SYSTEM`,
  `reason="inbound_callback_request"`) for the contact's phone **only if no voice consent record exists**
  (so a prior opt-out/REVOKE is respected, and no row-spam). Rationale: a patient who called in and asked to
  be called back has given an express basis for that callback. Legal-review note left in code + docs.

## CB-2 quiet-hours + double-contact
- On `ali/phase-2`, `hold` defers-and-resumes (intended) — the dev's `outbound-07-ai-callback/findings.md`
  D2/D4 assumed the old terminate→manual-queue. Reconcile that note.
- Double-contact guard: `Call.callback_resolved` exists (models/call.py). Add a skip in `_trigger_callback_async`:
  if the source Call is missing or already `callback_resolved`, don't enroll (staff already handled it).
  Residual race (resolved during the ETA delay) documented.

## PR-1 provisioning audit
- `admin_institutions.update_provisioning` (PATCH, L1606) and `clear_twilio_provisioning` (DELETE, L1638)
  take `_: User = Depends(get_current_admin)` (actor discarded) and never `log_audit`. `log_audit` +
  `AuditActor.ADMIN` + `AuditOutcome` imported already (L17-20); example call at L566. Use
  `AuditAction.INSTITUTION_UPDATE` (exists) with masked metadata (which fields changed; never the token/sid).

## XC-1b email idempotency
- `email_node_executor` POSTs to Resend (L117) with only Authorization/Content-Type headers. Add
  `Idempotency-Key: email:{run.id}:{node.id}` header (Resend supports it) so a crash-retry is vendor-deduped.
  (SMS/voice provider keys = documented follow-up; Twilio/Retell support varies.)

## Audit enum
- `AuditActor.ADMIN`, `AuditOutcome.SUCCESS`, `AuditAction.INSTITUTION_UPDATE` all exist (audit_log.py).
