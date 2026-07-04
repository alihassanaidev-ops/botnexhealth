# Progress: Outbound 07 — AI Callback

## Initial State
- needs_callback classification surfaces in a manual Callback Queue; no automation.
- Call model already has needs_callback status, preferred_callback_datetime, call_direction.
- Plan 03 outbound voice (VoiceNodeExecutor) committed — reused as the callback send.
- Branch note: CTO engine refactor (action_registry/build_dispatcher/usage_events) is on
  ANOTHER branch, NOT here. Built against this branch's direct-isinstance dispatcher.

## Slices

### Slice 1 — callback_requested trigger schema
- **Status:** complete ✅
- `definition_schema.py` — `CallbackRequestedTrigger` + added to `WorkflowTrigger` union + docstring.
- No separate trigger allowlist exists on this branch, so the union is the only registration.

### Slice 2 — CallbackTriggerService
- **Status:** complete ✅
- `callback_trigger_service.py` — `find_active_callback_workflows`, `compute_callback_eta`
  (future preferred time → eta; else immediate), `make_callback_idempotency_key`.

### Slice 3 — trigger_callback_workflows task
- **Status:** complete ✅
- `tasks/automation_workflow.py` — mirrors `trigger_appointment_workflows`; schedules
  `enroll_and_start_workflow_run.apply_async(..., eta=eta)` per active callback workflow.
  trigger_type="callback_requested", trigger_ref_type="call", idempotency = callback:{ver}:{call}.

### Slice 4 — Webhook enqueue
- **Status:** complete ✅
- `retell/webhooks.py` — guarded post-commit block: enqueue only when
  `call_status == needs_callback` AND `call_direction != outbound` (loop prevention).
  Passes preferred_callback_datetime through. try/except mirrors sibling enqueue blocks.

### Slice 5 — Tests
- **Status:** complete ✅
- `tests/unit/test_outbound_ai_callback.py` — 11 tests: trigger parses in definition; eta
  cases (none/past/naive/future); idempotency key; service filters by trigger_type;
  task schedules nothing / immediate eta (asserts kwargs) / honors future preferred time.
- Full unit suite: **1206 passed**, no regressions. (respx-broken files
  test_locations_routes.py / test_nexhealth_client.py ignored — pre-existing env issue.)

## No migration
Opt-in is via activating a callback_requested workflow, not a DB flag — no schema change.

## Loop prevention + fallback (verified by design)
- Loop: webhook skips outbound-originated calls; idempotency key = retell call id.
- Fallback: AI callback never sets callback_resolved → failed/held runs leave the call in
  the manual queue for staff. Voice max_attempts bounds dial retries.

## Merge into CTO branch — hand-off checklist
- Additive merge, no migration, dispatcher untouched (reuses wired `send_voice`).
- ⚠️ MUST add `"callback_requested"` to `SUPPORTED_TRIGGER_TYPES` (action_registry.py on the
  CTO branch — that allowlist doesn't exist here, so the trigger is invisible until merge).
- Watch for textual conflicts in `retell/webhooks.py` + `tasks/automation_workflow.py`
  (additive blocks — easy to resolve).

## Not-yet-done (out of Plan 07 backend scope)
- Clinic-facing setup depends on Plan 02 Builder UI (CTO lane) — no voice template exists.
- D2 quiet-hours clamp deferred (after-hours request ⇒ manual queue for now).

## Commands
- `APP_ENV=local uv run pytest tests/unit/test_outbound_ai_callback.py tests/unit/test_automation_plan09.py -q`
- Full: `APP_ENV=local uv run pytest tests/unit -q --ignore=tests/unit/test_locations_routes.py --ignore=tests/unit/test_nexhealth_client.py`
