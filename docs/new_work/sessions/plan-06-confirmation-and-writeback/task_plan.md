# Task Plan — Plan 06 C-1 (confirmed branch) + C-2 (PMS confirmation write-back)

**Branch:** `ali/phase-2` · **Alembic head:** `20260706_usage_cost_rollups` (single) · **No migration expected.**
**Scope:** make the Appointment Confirmation campaign functional end-to-end. C-3 (Sales Qualification)
DEFERRED; C-4/C-5 out of scope. Author on commit: Ali2047, no AI attribution.

## Goal
1. **C-1** — a patient's confirmation reply (inbound SMS) links to their WAITING confirmation run,
   writes `appointment_status="confirmed"` into `trigger_metadata`, cancels the wait timer, and resumes
   the run early so the ConditionNode takes the confirmed branch (`exit-confirmed`). No reply → 2h timer
   still falls through to `no_response` (unchanged).
2. **C-2** — on the confirmed branch, write the confirmation back to NexHealth via capability-gated
   `confirm_appointment` (`PATCH /appointments/{id}` `{"appt":{"confirmed":true}}`), fail-open + audited.

## Phases

### Phase 0 — Plan & investigate ✅ complete
Session folder created; architecture traced & re-verified (`findings.md`); C-2 support confirmed via
NexHealth docs; green baseline established.

### Phase 1 — Resolve decisions with user  ⏸ PARKED by owner (2026-07-05)
All C-1 product/safety decisions deferred at owner's request and documented in full in the root file
**`CONFIRMATION_MECHANISM_OPTIONS.md`**: (a) capture mechanism — Option A SMS-reply vs B PMS-poll vs Both;
(b) confirm reply keyword set; (c) the "CANCEL to cancel" wording/opt-out conflict; (d) Reactivation dead
branch. Factual C-2 ambiguity already RESOLVED (NexHealth supports `confirmed` write-back). Phases 2–4 do
NOT start until the owner picks the mechanism.

### Phase 2 — C-1 inbound-reply → run linkage + early resume  ⏳ pending
- `twilio_webhooks.py`: add confirmation classification AFTER STOP/START/HELP (never swallow opt-out);
  on a confirmation reply from a location-matched number, enqueue a resume task. Keep signature verify,
  TwiML response, and opt-out handling intact.
- `tasks/automation_workflow.py`: add `resume_sms_confirmation` / async, mirroring `resume_voice_outcome`
  — resolve contact by `hash_phone(From)`; find WAITING run(s) for (contact, location) whose current
  WaitNode's next node is a ConditionNode keying `appointment_status`; `cancel_timers_for_run`; write
  `appointment_status="confirmed"` + raw reply into `trigger_metadata`; `resume_after_timer`. At-most-once
  via `run.status==WAITING`.
- Tests: unit (classification, matching, at-most-once, non-confirm/no-reply → no_response, STOP still
  opts out) + extend integration.

### Phase 3 — C-2 capability-gated PMS write-back  ⏳ pending
- `pms/base.py`: add `SupportsAppointmentConfirmation` protocol (mirror `SupportsAppointmentTypeCreation`).
- `pms/nexhealth/adapter.py`: add `confirm_appointment(appointment_id)` cloning `cancel_appointment`.
- `api/models.py`: add `ConfirmAppointmentBody`/`Request` (or reuse the appt-body pattern).
- Resume task: after `resume_after_timer` returns `outcome=="confirmed"`, call write-back
  (gated on `trigger_ref_type=="appointment"` + NexHealth wiring + capability), fail-open + audited;
  internal-only + `unsupported` audit otherwise.
- Tests: write-back called only when confirmed + supported; fail-open on adapter error; skipped+audited
  when unsupported.

### Phase 4 — Verify & notate  ⏳ pending
- Full `tests/unit` green; integration green. `graphify update .`.
- Update `progress.md`, the Plan 06 report section (C-1 bug fixed), and the register (C-1/C-2 status;
  C-3 deferred). Commit only when asked.

## Regression risks (protect)
STOP/START/HELP opt-out path; Twilio signature verify; shared wait/hold-resume path (WaitNode, quiet-hours
hold, parked voice); cancellation cascade; send idempotency (`already_sent`); no double-contact;
revalidation fail-open; NO new caps.

## Decisions
- **C-2 mechanism (factual, RESOLVED):** NexHealth supports `confirmed` write-back → build it (not
  internal-only). Source: docs.nexhealth.com/reference/patchappointmentsid.
- (pending user) confirm mechanism; keyword set; CANCEL wording; Reactivation branch.
