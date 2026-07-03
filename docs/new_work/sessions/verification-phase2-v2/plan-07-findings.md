# Plan 07 — AI Callback Handling — Verification Findings

**Audited:** 2026-07-03
**Plan:** `docs/new_work/Implementation Plans/07-ai-callback-handling.md`
**Related session:** NONE (whole-codebase search performed)
**Method:** graphify orientation → targeted grep/read with `file:line` evidence.

## Verdict

**Status: NOT STARTED (~0% of Plan-07-specific work).** Every Plan-07 identifier
(`CallbackAutomationService`, `CallbackTriggerHandler`, `CallbackResolutionService`,
`callback_automation_settings`, `callback_workflow_links`, `callback_requested` trigger)
returns **zero matches** anywhere in `src/` or `tests/`. The only file containing these
strings is the plan document itself.

What exists is exactly the **pre-existing inbound callback queue** that the plan lists under
"Existing System Context" — none of it is new Plan-07 outbound-automation work. Plan 07
correctly depends on Plan 03 (voice), and was expected to be mostly not started.

## Evidence

### Plan-07 NEW work — ABSENT

- Grep for `callback_automation|CallbackAutomation|callback_workflow_link|CallbackTrigger|CallbackResolution|callback_requested` across whole repo → only hit is `docs/new_work/Implementation Plans/07-ai-callback-handling.md`.
- No callback references in `src/app/services/automation/` (grep `callback|Callback` → "No files found").
- Workflow trigger types supported today: only `appointment_offset` and `recall_scan`
  (`src/app/services/automation/appointment_trigger_service.py:29,39,45,55`). No `callback_requested` trigger.
- Campaign template registry has exactly the **4** Part-6 templates
  (`src/app/services/automation/campaign_templates.py:145-184`):
  `appointment-reminder-24h`, `appointment-confirmation-48h`, `recall-sms-6month`,
  `reactivation-sms-email-18month`. **No 5th AI-callback template** (the plan says Plan 07 owns it).
  Note: `campaign_templates.py:4-6` explicitly excludes voice templates because voice needs a
  clinic-specific Retell agent ID — so the callback template (which places an outbound AI call)
  could not be added here as-is anyway until Plan 03 lands.
- No migration for `callback_automation_settings` or `callback_workflow_links`
  (Glob `alembic/versions/*callback*` → none; grep of `alembic/` for both table names → none).
- `PostCallService` does NOT enroll callbacks into any workflow. It only normalizes the status
  string `"needs callback" → CallStatus.NEEDS_CALLBACK.value` (`src/app/services/post_call_service.py:35`).
  No call to a `CallbackAutomationService`, no enrollment, no `callback_workflow_links` write.
- No per-location manual-vs-AI config toggle anywhere.

### Pre-existing INBOUND callback queue — PRESENT (not Plan-07 work)

- `src/app/api/routes/callbacks.py` — dedicated callback queue API (paginated/filterable list of
  calls needing callbacks). `CallbackItem` model exposes `callback_resolved`,
  `callback_resolved_at`, `callback_note`, `preferred_callback_datetime` (`callbacks.py:38-50`).
- `Call` model fields `preferred_callback_datetime`, `callback_resolved`, `callback_resolved_at`,
  `callback_note` exist (`src/app/models/call.py`, baseline migration
  `alembic/versions/20260510_consolidated_baseline.py`; also `20260622_nopms_call_status.py`).
- `CallStatus.NEEDS_CALLBACK` and post-call normalization (`post_call_service.py:35`).
- SSE/notification event `callbacks_updated` and staff notifications (pre-existing;
  `notification_service.py`, `event_bus.py`, `useSSE.ts`, `Callbacks.tsx`).
- Frontend callback queue page `nexus-dashboard-web/src/pages/Callbacks.tsx` (manual staff surface).

All of the above is the **manual fallback** the plan intends to preserve — it is prior work, not Plan 07.

## Deliverables checklist (from plan §"New Components Required")

| Deliverable | Status | Evidence |
|---|---|---|
| `callback_automation_settings` table (mode manual/ai_auto/ai_after_staff_review) | MISSING | no migration, no model |
| `callback_workflow_links` table (status, unique on call_id) | MISSING | no migration, no model |
| Extend workflow trigger payload (inbound call id, contact, reason, preferred time) | MISSING | trigger types only appointment_offset/recall_scan |
| `callback_requested` trigger wiring into engine | MISSING | not in trigger service |
| Per-location manual-vs-AI config toggle | MISSING | no config surface |
| AI-callback workflow template (5th palette template) | MISSING | only 4 templates in campaign_templates.py |
| Capture preferred callback time as structured trigger input | MISSING (field pre-exists on Call, not wired to any trigger) | `call.py` field exists, no trigger uses it |
| `CallbackAutomationService` | MISSING | no such class |
| `CallbackTriggerHandler` | MISSING | no such class |
| `CallbackResolutionService` | MISSING | no such class |
| Post-call hook → enroll needs_callback into workflow | MISSING | post_call_service only normalizes status |

## Tests

- No tests reference callback automation. The 14 test files matching `callback` all concern the
  **pre-existing inbound queue / call model**: `test_call_status_normalization.py`,
  `test_call_phi_reveal.py`, `test_call_model_indexes.py`, `test_notifications_sse_wiring.py`,
  `test_email_notification_privacy.py`, `test_retention_policy.py`, RLS/tenant-scope tests, etc.
- None of the plan's Validation Strategy items (automation setting decisions, preferred-time
  normalization, one-workflow-link-per-callback integration test, manual-resolution cancels
  queued automation, RLS on callback links, E2E enrollment→wait→outbound→resolve) exist.

## Notes

- Plan depends on Plan 03 (outbound voice `SendVoiceNode`) which is being built separately;
  the callback template cannot be fully realized until that lands. Not-started status is expected.
- `preferred_callback_datetime` already exists as a timezone-aware `Call` column, so the
  plan's "migrate custom-field capture → Call column" concern is partly moot — but nothing
  consumes it for automation yet.
