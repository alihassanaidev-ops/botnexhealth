# Findings — Plan 06 C-1 (confirmed-branch) + C-2 (PMS confirmation write-back)

Session started 2026-07-05. Branch `ali/phase-2` @ `36bcaa0` (local == origin/ali/phase-2).
Alembic single head: **`20260706_usage_cost_rollups`** (verified `python -m alembic heads`).

All `file:line` below re-verified against the current tree (not taken from the stale report/register).

---

## Architecture trace — Confirmation flow (verified)

### The template (the dead branch)
`src/app/services/automation/campaign_templates.py:46-77` — `_APPOINTMENT_CONFIRMATION_48H`:
- `send_sms` "sms-confirm" (body now: "Reply YES to confirm. Reply STOP to opt out.")
  → `wait-response`
- `wait` "wait-response" duration 7200s (2h) → `check-confirmed`
- `condition` "check-confirmed" rule `{field: appointment_status, op: eq, value: confirmed}`;
  true → `exit-confirmed` (outcome `confirmed`), false → `exit-no-response` (outcome `no_response`).
- **Nothing ever writes `appointment_status` into run context → condition always false → always
  `no_response`.** Confirmed. This is C-1.

Reactivation template (`:96-138`) had the same class of dead branch on `appointment_booked` (`:118`).
It is now fixed event-led: accepted NexHealth appointment created/updated events enqueue
`resume_reactivation_booking` for the resolved contact/location.

### How the condition reads context
`step_dispatcher.py:355-369` `_evaluate_rule` → `context.get(rule.field)`. The `context` passed into
`resume_after_timer`/`advance` IS the run's `trigger_metadata` (see enrollment `:443` and voice-resume
`:848`). So **writing `appointment_status="confirmed"` into `run.trigger_metadata` and resuming makes the
condition read `confirmed`.**

### WaitNode resume semantics (the early-resume seam)
`step_dispatcher.py:257-333` `resume_after_timer`:
- Guards `run.status == WAITING` (`:283`) — makes reply/timer race at-most-once.
- For a WaitNode, moves the pointer **past** the wait to `current_node.next_node_id` (`:323`) — i.e. to
  the ConditionNode — then `advance()` evaluates it against `context`.
- So an early resume (reply arrives before the 2h timer) with `appointment_status=confirmed` in context
  → condition true → `exit-confirmed`. Exactly the desired behavior. If no reply, the 2h timer fires the
  same path with no `appointment_status` → false → `no_response` (unchanged).

### The resume pattern to mirror (voice)
`tasks/automation_workflow.py:735-856` `resume_voice_outcome` / `_resume_voice_outcome_async`:
Celery task enqueued from the Retell webhook. Finds the parked step, checks `run.status==WAITING`,
`cancel_timers_for_run`, writes outcome into `run.trigger_metadata`, `build_dispatcher(...)` with
`PmsLiveRevalidationService`, `dispatcher.resume_after_timer(run, definition, context=md, ...)`, commit.
**C-1 mirrors this for SMS confirmation.**

### Run ↔ patient linkage (correlation for inbound reply)
- `AutomationWorkflowRun` (`models/automation_workflow.py:226-299`): has `contact_id` (FK contacts,
  nullable, indexed `ix_automation_workflow_runs_contact`), `location_id`, `status`, `current_step_id`,
  `trigger_type`, `trigger_ref_type`, `trigger_ref_id`, `trigger_metadata` (JSONB).
- SMS send resolves recipient via `run.contact_id → Contact.phone` (`sms_node_executor.py:52-67`).
- `Contact.phone` setter stores `phone_hash = hash_phone(value)` (`models/contact.py:138-142`);
  `Contact.find_by_phone_hash(phone)` returns the hash for a WHERE clause (`:152-155`).
- **Correlation:** inbound `From` → `hash_phone(From)` → Contact(s) in the institution → WAITING runs
  for that `contact_id` at that `location_id`. Robust and RLS-scoped.

### Inbound SMS webhook (where the reply lands today)
`api/routes/twilio_webhooks.py:74-149` `inbound_sms`:
- `_verified_form` (`:205`) verifies Twilio signature (sub-account token via
  `TenantTwilioCredentialResolver`) — must stay intact.
- Resolves location by `To` number (`_location_for_twilio_number:240`).
- `_classify_intent` (`:57-71`) → STOP / START / HELP handled first (suppress / release / help).
- **Any other reply falls through to `:141-149`: logged and ignored.** ← where confirmation handling
  hooks in, strictly AFTER the STOP/START/HELP branches (never swallow opt-out).

---

## C-2 factual resolution — NexHealth DOES support confirmation write-back

- NexHealth `PATCH /appointments/{id}` patchable fields: `confirmed`, `cancelled`, `checkin_at`,
  `start_time`, `end_time`, `operatory_id`, `note`. Body shape `{"appt": {"confirmed": true}}`.
  **Confirmation transition restricted to `confirmed=false → true`.**
  Sources: https://docs.nexhealth.com/reference/patchappointmentsid (fetched 2026-07-05).
- Existing adapter mirror: `cancel_appointment` (`pms/nexhealth/adapter.py:442-456`) already does
  `PATCH /appointments/{id}` with `{"appt": {"cancelled": true}}` via `CancelAppointmentRequest`.
  **`confirm_appointment` is a direct clone** with `{"appt": {"confirmed": true}}`.
- Adapter has NO confirm/update-status method today (verified: book `:409`, cancel `:442`, reschedule
  `:458`, update_appointment_type `:535`, get_appointment `:313`). Confirms C-2 premise.
- Capability-gating pattern exists: `pms/base.py` has `SupportsAppointmentTypeCreation` (`:124`),
  `SupportsAvailabilityLinking` (`:148`) protocols — mirror with a `SupportsAppointmentConfirmation`.
- `run.trigger_ref_type == "appointment"` + `run.trigger_ref_id == appointment_id` carry the target
  (same as `PmsLiveRevalidationService.revalidate` uses, `revalidation.py:89-96`). Adapter construction
  pattern: `NexHealthAdapter.create(institution, location)` gated on
  `location.nexhealth_subdomain and location.nexhealth_location_id` (`revalidation.py:117-125`).

---

## Design decisions (grounded)

**C-1 mechanism:** inbound-SMS reply → run linkage + early resume (Celery task mirroring
`resume_voice_outcome`). Webhook stays fast (returns TwiML), enqueues `resume_sms_confirmation`.

**Which runs to match (template-agnostic, safe):** WAITING run for (contact, location) whose current
node is a WaitNode AND its `next_node_id` is a ConditionNode with a rule `field == "appointment_status"`.
This confirms ONLY confirmation-style runs — reactivation runs key `appointment_booked` and are NOT
matched, so a "YES" during a reactivation wait cannot wrongly confirm.

**Reactivation booked mechanism:** accepted NexHealth appointment created/updated events already resolve
`contact_id` and `location_id` in `nexhealth_webhooks.py`. When present, the webhook enqueues
`resume_reactivation_booking`, which targets WAITING runs for the same contact/location whose current WaitNode
flows into a ConditionNode reading `appointment_booked`. It writes `appointment_booked=true` and
`booked_appointment_id`, cancels the 48h wait timer, and resumes to `exit-booked`. This uses the codebase's
existing appointment-event signal instead of unverified PMS polling or ambiguous SMS free text.

**C-2 seam:** in the resume task, after `resume_after_timer` returns with `outcome == "confirmed"`, call
capability-gated `confirm_appointment(trigger_ref_id)`, fail-open + audited on error (never breaks the
patient-facing flow, never raises). If PMS/location not wired or PMS lacks the capability → record
internally only + audit `unsupported`. Ties write-back to the branch actually firing (respects
revalidation + at-most-once).

**No migration** — writes into existing `trigger_metadata` JSONB; write-back is an adapter call.

---

## New findings / risks not previously tracked

1. **Template/opt-out conflict (pre-existing, fixed):** the confirmation body said "Reply CANCEL to cancel" but
   `CANCEL` ∈ `STOP_KEYWORDS` (`twilio_webhooks.py:45`) → a patient replying "CANCEL" is **opted out of
   all SMS**, not routed to appointment cancellation. The template now removes "CANCEL to cancel"; real
   cancel-from-SMS remains separate C-5/write-back-cancel territory.
2. Confirmation keyword set must not overlap STOP/START/HELP. Implemented owner choice
   `YES/Y/CONFIRM/C/1`; none overlap. Only bare single-token replies confirm, so mixed/ambiguous replies
   remain ignored.
3. `resume_after_timer` re-runs `advance()` which for a ConditionNode does NOT re-send (condition→exit,
   no send node on the confirm branch) → no double-contact risk on confirm.
