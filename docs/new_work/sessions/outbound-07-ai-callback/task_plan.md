# Task Plan: Outbound 07 — AI Callback

## Goal
Turn a `needs_callback` inbound-call classification into an optional, per-clinic
**AI-handled outbound callback**: the existing "callback requested" signal becomes
a workflow trigger that auto-enrolls the contact into an outbound workflow, and the
outbound Retell agent (Plan 03) calls the patient back — instead of a human working
the manual Callback Queue.

## Decisions (locked with user 2026-07-04)

| # | Decision | Choice | Rationale |
|---|----------|--------|-----------|
| D1 | Default posture | **Manual queue default; AI callback is per-clinic opt-in** | Safe — no clinic gets surprise robocalls. Scope explicitly flags this (§ lines 631/747). |
| D2 | Requested callback time | **Honor `preferred_callback_datetime` when present (wait-until, clamped to quiet hours); else next open window** | Field already exists; reuse timezone/quiet-hours logic. |
| D3 | Enrollment trigger mechanism | **Event-driven from the post-call webhook** (where `needs_callback` is classified) | No polling lag; classification already lands there. |
| D4 | Loop prevention + failure fallback | **Don't re-enroll outbound-originated calls; after N failed attempts fall back to manual queue** (not infinite retry) | Prevents callback→needs_callback→callback loops; bounded cost. |

## Current Status
**Complete** ✅ — all slices shipped. 1206 unit tests pass (11 new), no regressions.
No migration required (opt-in via workflow activation — see D1 refinement in findings.md).

## Decision refinements (branch-adjusted — see findings.md)
- **D1 → opt-in via workflow activation, NO new column/migration.** A clinic enables AI
  callback by activating a `callback_requested` workflow; none active ⇒ manual queue.
  Mirrors appointment_offset/recall_scan; deactivating = kill-switch.
- **D2 → honor `preferred_callback_datetime` when future, else immediate.** Quiet-hours
  clamp deferred: on THIS branch the gate `hold` terminates the run (no next-window
  defer — that's the CTO branch), so a quiet-hours time ⇒ held ⇒ stays in manual queue.
- **D4 → satisfied by the "never mark resolved" invariant** + direction guard + idempotency
  key. Failed/held callbacks leave the call `needs_callback` + unresolved ⇒ manual queue.
  Voice `max_attempts` bounds dial retries. No extra retry code.

## Slices (final)
- Slice 1 — `CallbackRequestedTrigger` schema + union — **done** ✅
- Slice 2 — `callback_trigger_service.py` (find workflows, eta, idempotency key) — **done** ✅
- Slice 3 — `trigger_callback_workflows` Celery task (enqueue-with-eta) — **done** ✅
- Slice 4 — Webhook post-commit enqueue (guarded: needs_callback AND not outbound) — **done** ✅
- Slice 5 — Tests (`test_outbound_ai_callback.py`, 11 tests) — **done** ✅

## Merge note → CTO branch (IMPORTANT)
This branch was built WITHOUT the CTO's engine refactor. When Plan 07 merges into the
CTO branch, the merge is mostly additive (new service, new task, new trigger union
member, new webhook block, new tests; **no migration**; dispatcher untouched — reuses
already-wired `send_voice`). But one item must be done by hand on the merged result:

- ⚠️ **Add `"callback_requested"` to the `SUPPORTED_TRIGGER_TYPES` frozenset** in the CTO
  branch's `action_registry.py`. That allowlist does NOT exist on this branch, so the
  trigger is invisible until merge; without this the trigger won't be discovered.
- Minor: possible textual conflicts in `retell/webhooks.py` and `tasks/automation_workflow.py`
  if the CTO edited the same regions — additive blocks, easy to resolve.

## Completion caveats (feature is backend-complete, not yet clinic-configurable)
- **Depends on Plan 02 (Builder UI, CTO lane)** for a clinic to create/activate a
  `callback_requested` workflow. No pre-built template (voice templates excluded — need
  per-clinic `retell_agent_id`). Backend fully accepts/runs one; today only via API/JSON.
- **D2 quiet-hours clamp deferred** — after-hours requested time ⇒ manual queue (not
  next-open-window). Closes for free once the CTO dispatcher's hold→next-window defer merges.

## Files touched
| File | Change |
|------|--------|
| `src/app/services/automation/definition_schema.py` | +CallbackRequestedTrigger + union + docstring |
| `src/app/services/automation/callback_trigger_service.py` | New |
| `src/app/tasks/automation_workflow.py` | +trigger_callback_workflows task + imports |
| `src/app/retell/webhooks.py` | +guarded AI-callback enqueue after commit |
| `tests/unit/test_outbound_ai_callback.py` | New — 11 tests |
