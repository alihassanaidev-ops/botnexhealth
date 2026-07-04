# Task Plan — Follow-ups & Bugs Closeout (before Plan 03 Outbound Voice)

**Started:** 2026-07-04
**Branch:** `ali/phase-2` (currently `754f953`, Plan 07 merged)
**Class:** C/D (multi-file; compliance-correctness critical)
**Goal:** resolve the concrete open follow-ups and bugs surfaced in the recent review cycles so the
board is clean before starting Plan 03. Register = `../outbound-followups-and-gaps.md`.

## Scope (concrete, resolvable now — NOT whole future plans)
- **XC-6 / CB-3 (P0 blocker) — consent CAPTURE path.** The only `ConsentRecord` writers hardcode
  `channel=sms`; nothing records voice/email consent → the gate blocks every workflow voice & email send
  (`no_voice_consent`/`no_email_consent`). This makes the merged Plan 07 AI-callback non-functional
  end-to-end. **Fix:** (a) make `record_consent` / `record_consent_identity` channel-parameterized (default
  SMS, backward-compatible); (b) capture an express **VOICE** consent when a patient's inbound call requests
  a callback (the patient asked to be called back = express basis; `source=system`), so callback runs pass
  the gate. Email capture stays a Plan-05 intake concern but is now *enabled* by (a).
- **CB-2 (P1) — quiet-hours + double-contact.** Document the verified defer-and-resume behavior (intended),
  reconcile the dev's `outbound-07-ai-callback/findings.md`, and add a **callback-resolved skip guard** so an
  already-resolved manual callback isn't also AI-dialed (reduce staff+AI double-contact).
- **PR-1 (P1 bug) — audit provisioning credential changes.** `admin_institutions.py` PATCH/DELETE of
  Twilio/email creds are not audit-logged (violates Plan-10 audit requirement). Add `log_audit`.
- **XC-1b (partial) — email crash-window idempotency.** Add Resend `Idempotency-Key` header (executor already
  computes the key) so a crash-retry can't double-send email. Document SMS/voice provider-key as follow-up.

## Explicitly NOT in this closeout (documented, deferred)
- Plan 03's own items (V-1 outcome loop, V-3 consent-basis hard-block, V-4 data model, V-6 retry) — that's
  the **next** major task (Outbound Voice).
- Plan 06 C-1 dead confirm-branch (needs response capture / PMS write-back — Plan 04/06).
- CB-4 packaged AI-callback template + dedicated tables (leaner opt-in-via-activation design accepted).
- Email consent *capture UI/intake* (Plan 05); SMS/voice provider idempotency keys (XC-1b remainder).

## Phases
- [x] P0 Research — record_consent callers (ensure channel default keeps SMS working); callback trigger task;
  provisioning endpoints; email executor idempotency key. → findings.md
- [x] P1 XC-6 consent capture (channel-generic writer + voice-consent-on-callback-request)
- [x] P2 CB-2 (callback-resolved guard + docs)
- [x] P3 PR-1 (provisioning audit)
- [x] P4 XC-1b email idempotency header
- [x] P5 Tests + full unit + real-Postgres integration + graph update; notate everything; update report + register

## Status
**COMPLETE (2026-07-04).** XC-6, CB-2, CB-3, PR-1, XC-1b resolved; Plan 07 now functional end-to-end.
1341 unit + 8 integration green. Deferrals documented. See `progress.md`. Ready for Plan 03 next.
