# Task Plan — Plan 06 C-1 (confirmed branch) + C-2 (PMS confirmation write-back)

**Branch:** `ali/phase-2` · **Alembic head:** `20260706_usage_cost_rollups` (single) · **No migration expected.**
**Scope:** make the Appointment Confirmation campaign functional end-to-end and fix the Reactivation
`appointment_booked` dead branch via existing NexHealth appointment events. C-3 (Sales Qualification)
DEFERRED; C-4/C-5 out of scope. Author on commit: Ali2047, no AI attribution.

## Goal
1. **C-1** — a patient's confirmation reply (inbound SMS) links to their WAITING confirmation run,
   writes `appointment_status="confirmed"` into `trigger_metadata`, cancels the wait timer, and resumes
   the run early so the ConditionNode takes the confirmed branch (`exit-confirmed`). No reply → 2h timer
   still falls through to `no_response` (unchanged).
2. **C-2** — on the confirmed branch, write the confirmation back to NexHealth via capability-gated
   `confirm_appointment` (`PATCH /appointments/{id}` `{"appt":{"confirmed":true}}`), fail-open + audited.
3. **Reactivation booked branch** — when NexHealth reports a new/rescheduled appointment for the same
   contact/location, resume WAITING reactivation runs, write `appointment_booked=true`, and take the
   `exit-booked` branch instead of sending the follow-up email.

## Phases

### Phase 0 — Plan & investigate ✅ complete
Session folder created; architecture traced & re-verified (`findings.md`); C-2 support confirmed via
NexHealth docs; green baseline established.

### Phase 1 — Resolve decisions with user  ✅ complete
Owner/developer decisions received 2026-07-06:
- Confirmation capture: Option A, inbound SMS reply only this pass; defer NexHealth confirmed-flag polling
  until live-tenant sync behavior is verified.
- Confirmation keywords: Option B from the user prompt, implemented as `YES`, `Y`, `CONFIRM`, `C`, `1`.
- CANCEL wording conflict: Option A, remove "CANCEL to cancel" from the confirmation SMS.
- Reactivation dead branch: fix now, using existing NexHealth appointment created/updated events as the
  booking signal rather than SMS free-text or PMS polling.

### Phase 2 — C-1 inbound-reply → run linkage + early resume  ✅ implemented
- `twilio_webhooks.py`: classifies bare confirmation tokens only after STOP/START/HELP handling, then
  enqueues `resume_sms_confirmation`; opt-out/help/start paths remain first and unchanged.
- `tasks/automation_workflow.py`: added `resume_sms_confirmation` and shared context-field resume helper.
  It resolves `From` by `Contact.phone_hash`, finds WAITING runs for the contact/location whose current
  WaitNode flows into a ConditionNode reading `appointment_status`, cancels pending timers, writes
  `appointment_status="confirmed"` plus reply metadata, and resumes through `resume_after_timer`.
- Tests cover keyword classification and field-specific run matching.

### Phase 3 — C-2 capability-gated PMS write-back  ✅ implemented
- `pms/base.py`: added `SupportsAppointmentConfirmation`.
- `pms/nexhealth/adapter.py`: added `confirm_appointment(appointment_id)` using
  `PATCH /appointments/{id}` with `{"appt":{"confirmed":true}}`.
- `api/models.py`: added `ConfirmAppointmentBody`/`ConfirmAppointmentRequest`.
- Resume task: after confirmed branch completion, calls write-back for appointment-triggered runs only.
  PMS write-back is fail-open and writes a `CONFIRM_APPOINTMENT` audit row with success/failure metadata.
- Tests cover the NexHealth confirm payload.

### Phase 3b — Reactivation booked branch  ✅ implemented
- `nexhealth_webhooks.py`: after a non-cancelled new/rescheduled appointment event is accepted and queued
  for appointment-offset workflows, enqueue `resume_reactivation_booking` when the patient contact is known.
- `tasks/automation_workflow.py`: `resume_reactivation_booking` uses the same shared context-field resume
  helper, but targets `appointment_booked` and writes `booked_appointment_id` metadata. This keeps the
  Reactivation fix event-led and avoids unverified PMS polling or ambiguous SMS free-text booking intent.
- Tests cover webhook enqueue behavior and field-specific matching.

### Phase 4 — Verify & notate  ⏳ in progress
- Focused unit verification:
  `APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/unit/test_inbound_sms_intent.py tests/unit/test_automation_campaign_templates.py tests/unit/test_nexhealth_adapter_appointments.py tests/unit/test_automation_workflow_task.py tests/unit/test_nexhealth_appointment_webhook.py`
  → **100 passed, 1 warning**.
- Focused lint:
  `APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run ruff check ...`
  → **All checks passed**.
- Pending before handoff/commit: `graphify update .`.

## Regression risks (protect)
STOP/START/HELP opt-out path; Twilio signature verify; shared wait/hold-resume path (WaitNode, quiet-hours
hold, parked voice); cancellation cascade; send idempotency (`already_sent`); no double-contact;
revalidation fail-open; NO new caps.

## Decisions
- **C-2 mechanism (factual, RESOLVED):** NexHealth supports `confirmed` write-back → build it (not
  internal-only). Source: docs.nexhealth.com/reference/patchappointmentsid.
- **Mechanism (RESOLVED by dev 2026-07-06): Option A (inbound SMS reply) only this pass; Option B
  (PMS-poll safety net) DEFERRED** pending live-tenant verification that NexHealth reliably syncs the
  `confirmed` flag. Rationale: A matches the SMS's own "Reply YES" CTA, is real-time, reuses the proven
  `resume_voice_outcome` pattern, and adds zero NexHealth-key load; B is an unverified external dependency
  (CTO open-question #1) — will not ship unverified external-API behavior. Phone/front-desk confirmers fall
  through to `no_response` safely.
- **Keyword set (RESOLVED): `YES, Y, CONFIRM, C, 1`** per owner choice. Implementation accepts only a
  single bare token; mixed replies such as "yes but reschedule" are ignored, preserving PMS write-back safety.
- **"CANCEL to cancel" wording (RESOLVED): Option A — reword** to drop CANCEL (it's a STOP keyword →
  silently opts the patient out). Keep "Reply YES to confirm. Reply STOP to opt out." Real cancel-from-SMS
  is a separate feature.
- **Reactivation dead branch (RESOLVED): Option B — fix now** using NexHealth appointment created/updated
  events for the same contact/location. Rationale: an accepted appointment event is the product-grade booking
  signal already present in this codebase; it avoids PMS polling load and avoids guessing from SMS free text.
