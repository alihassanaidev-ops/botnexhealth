# Findings: Outbound 07 — AI Callback

## Branch note (IMPORTANT)
Plan 03 is committed (`0519e28`). The CTO's engine refactor (action_registry,
build_dispatcher, revalidation, calendar jitter, `20260704_usage_events` migration)
is on a **different branch** — NOT here. A recon subagent conflated the two and
fabricated `action_registry.py` (does not exist on this branch). All facts below are
verified against the current working tree.

Current branch reality:
- Dispatcher = direct `isinstance` branches (SMS/email/voice), no action registry.
- Trigger types live ONLY in the Pydantic union in `definition_schema.py` — there is
  NO `SUPPORTED_TRIGGER_TYPES` frozenset to update. Adding a union member is the only
  schema registration needed.
- Migration head = `20260703_retell_from_number` (chains from `20260703_provisioning`).

## Verified facts

### Enrollment — `enrollment_service.py:30`
`AutomationWorkflowEnrollmentService(session).enroll(*, institution_id, workflow_id,
workflow_version_id, contact_id=None, location_id=None, trigger_type=None,
trigger_ref_type=None, trigger_ref_id=None, trigger_metadata=None, idempotency_key=None)
-> (run, created)`. Idempotency-key dedup + conflicting-active-run guard. Reuse as-is.

### Delayed first execution — idiomatic pattern (`tasks/automation_workflow.py`)
`trigger_appointment_workflows` (task) → for each active wf, `compute_enrollment_eta` →
`enroll_and_start_workflow_run.apply_async(kwargs=..., eta=eta, queue="workflow")`.
i.e. **Celery `eta` on the enroll task** is how a run's first send is delayed — NOT a
run.scheduled_at field, NOT an initial WaitNode. `enroll_and_start_workflow_run` enrolls
then immediately `dispatcher.advance()`. Reuse this exact mechanism for callback timing.

### Triggers — `definition_schema.py:19-54`
Classes: AppointmentOffsetTrigger, RecallScanTrigger, ManualTrigger, BulkImportTrigger;
`WorkflowTrigger` discriminated union at :46. Add `CallbackRequestedTrigger`.

### Webhook hook point — `retell/webhooks.py`
`saved_call = await post_call_service.process_call_analyzed_event(...)` at :401,
`session.commit()` at :410, then post-commit enqueue blocks (recording/email/in-app) at
:412-470, each a try/except that imports an enqueue helper. Insert the callback-trigger
enqueue as one more such block. Available: `saved_call.id/.contact_id/.call_status/
.call_direction`, `institution.id`, `location.id if location else None`.

### Call model — `models/call.py` (verified earlier)
`CallStatus.NEEDS_CALLBACK = "needs_callback"` (:56); `preferred_callback_datetime`
(:226); `call_direction` (:156) w/ `CallDirection.OUTBOUND = "outbound"` (:81);
`callback_resolved` (:229). Loop prevention = skip when `call_direction == "outbound"`.

## Design decisions (branch-adjusted)

- **Opt-in via workflow activation (refines D1, no new column).** A clinic enables AI
  callback by activating a workflow whose `trigger_type == "callback_requested"`. No
  active callback workflow ⇒ manual queue (default). Mirrors appointment_offset /
  recall_scan exactly; avoids a redundant boolean + migration. Deactivating the workflow
  is the kill-switch. Still "opt-in, manual default" per D1.
- **D2 timing (v1).** eta = `preferred_callback_datetime` if in the future, else enroll
  immediately. Quiet-hours CLAMPING deferred: on this branch `hold` terminates the run as
  `compliance_hold` (no next-window defer — that's the CTO branch). So a requested time
  that lands in quiet hours ⇒ gate holds ⇒ run ends ⇒ call stays `needs_callback` +
  unresolved ⇒ **manual queue**. Acceptable v1: honors requested time in the common case
  (business-hours requests), degrades to manual otherwise. Clean seam left for a
  next-open-window clamp later.
- **D4 fallback = the "never mark resolved" invariant.** AI callback never sets
  `callback_resolved=True`; only a human (or a future explicit resolve) does. So any
  failed/held callback run automatically leaves the call in the manual queue. Voice
  `max_attempts` (node field, 1-3) bounds dial retries. Loop prevention via direction
  guard + idempotency_key = the retell call id.

## Slice mechanics
- New file `callback_trigger_service.py`: `find_active_callback_workflows(institution_id)`,
  `compute_callback_eta(preferred_dt, now)`, `make_callback_idempotency_key(version_id, call_id)`.
- New task `trigger_callback_workflows` in `tasks/automation_workflow.py` mirroring
  `trigger_appointment_workflows` (enqueue-with-eta).
- Webhook: guarded post-commit enqueue (needs_callback AND direction != outbound).
