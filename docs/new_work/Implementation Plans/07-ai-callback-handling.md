# Part 7 - AI Callback Handling Implementation Plan

## What Needs To Be Built

Build optional automation for inbound callback requests. Today, callback requests appear in the dashboard callback queue for staff to handle manually. The new capability lets each clinic choose whether selected callback requests are routed into an outbound workflow where the AI outbound voice agent calls the patient, honors any preferred callback time, books or resolves the need when possible, and updates the original callback item.

## Existing System Context

The backend already has:

- `CallStatus.NEEDS_CALLBACK`.
- Callback queue API in `src/app/api/routes/callbacks.py`.
- `Call.preferred_callback_datetime`, `callback_resolved`, `callback_resolved_at`, and `callback_note`.
- Post-call status normalization mapping "Needs callback" into `needs_callback`.
- Contact creation/upsert and location access grants from post-call ingestion.
- In-app notifications and SSE event type `callbacks_updated`.
- Custom fields infrastructure that can capture extra Retell analysis values, though preferred callback time is now also present as a call column.

Current gaps:

- No configuration decides manual vs AI callback handling per clinic/location/campaign.
- No trigger enrolls callback rows into a workflow.
- No durable wait-until-preferred-time scheduling.
- No automatic update from outbound call outcome back to the original callback queue item.

## Existing Components To Reuse

- Existing callback queue as the manual fallback and operator visibility surface.
- Existing `Call` callback fields.
- Existing `PostCallService` callback classification.
- Existing notifications/SSE for staff updates.
- Outbound voice calling action from Part 3.
- Workflow engine trigger/run model from Part 1.
- Existing contact and phone reveal controls for staff visibility.

## New Components Required

### Data Model

- `callback_automation_settings`
  - `institution_id`, `location_id`
  - mode: `manual`, `ai_auto`, `ai_after_staff_review`
  - workflow/template id to enroll into
  - allowed callback windows
  - max attempts and fallback action
  - enabled flag

- `callback_workflow_links`
  - `institution_id`, `location_id`
  - inbound `call_id`
  - `contact_id`
  - `workflow_run_id`
  - status: `pending`, `scheduled`, `in_progress`, `resolved`, `failed`, `fallback_to_staff`
  - `preferred_callback_datetime`
  - timestamps
  - unique on inbound `call_id` to prevent duplicate automation

- Extend workflow trigger payload to include:
  - original inbound call id
  - contact id
  - callback reason/summary reference
  - preferred callback datetime

### AI Callback Workflow Template

This plan **owns the AI-callback workflow template** that `callback_automation_settings` enrolls
into (the 5th template surfaced in the Part 2 builder palette, alongside Part 6's four campaigns).
It is a system template like the others: wait-until-preferred-time → place outbound AI call →
branch on outcome (booked/resolved → resolve callback; voicemail/no-answer → retry per config;
exhausted/unsatisfied → fall back to staff queue). Kept in sync with the Part 6 template set so the
palette matches what exists.

### Services

- `CallbackAutomationService`
  - evaluates location settings after a callback-classified call is persisted
  - decides manual vs workflow enrollment
  - creates `callback_workflow_links`
  - schedules the workflow at preferred time or next allowed callback window

- `CallbackTriggerHandler`
  - workflow trigger implementation for `callback_requested`
  - validates contact has reachable phone and not suppressed
  - creates a workflow run linked to original call

- `CallbackResolutionService`
  - maps outbound call outcomes to callback queue state
  - marks original `Call.callback_resolved` when goal is met
  - writes callback notes without storing unnecessary PHI in workflow state
  - falls back to staff when attempts are exhausted

## End-To-End Implementation Approach

1. Add callback automation settings per location.
2. Add callback workflow link table with RLS and unique inbound call constraint.
3. Extend post-call processing after `Call` creation: if status/tags include `needs_callback`, call `CallbackAutomationService`.
4. If mode is manual, keep current queue behavior unchanged.
5. If mode is AI, create a workflow enrollment linked to the callback.
6. Workflow waits until `preferred_callback_datetime` in the location timezone when present; otherwise uses configured callback window.
7. Workflow places outbound AI call.
8. Retell post-call webhook updates the outbound call and workflow attempt.
9. `CallbackResolutionService` updates original queue row to resolved or fallback state.
10. Publish `callbacks_updated`, `calls_updated`, and campaign/workflow progress events.

## Architecture Decisions

- Preserve manual callback queue as the source of operational truth for staff. AI automation adds linked workflow state, not a replacement queue.
- Use the original inbound `Call` row as the callback request record. Avoid creating a second callback entity unless the queue requirements outgrow `Call`.
- Link one callback request to at most one active automation run. This prevents duplicate AI callbacks.
- Preferred callback time is interpreted in the clinic location timezone unless patient timezone is explicitly known in a later phase.
- Failed or exhausted automation must fall back to the existing staff queue.

## Technical Considerations

- `preferred_callback_datetime` is timezone-aware in the model; ensure extraction from Retell/custom fields normalizes into the location timezone correctly.
- If existing custom field capture is used for callback time, plan a migration path into `Call.preferred_callback_datetime`.
- Staff may manually resolve a callback while AI automation is queued. Workflow must cancel or no-op before placing the call.
- Outbound AI call should receive enough context to help the patient, but only minimum necessary details and no raw transcript.
- Workflow state should reference call/contact ids, not duplicate summary/transcript PHI.
- Callback automation settings are sensitive configuration and should be audited.

## Dependencies

- Durable workflow engine with wait-until scheduling.
- Outbound voice action.
- Quiet-hours/send-window service.
- Callback configuration UI.
- Campaign/workflow progress UI.
- Cross-channel suppression.

## Edge Cases

- Callback request has no reachable phone.
- Preferred callback time is in the past.
- Preferred callback time falls outside quiet hours.
- Patient opted out after requesting callback.
- Staff resolves callback before AI run starts.
- AI call reaches voicemail; workflow should retry or notify staff depending on configuration.
- AI books appointment; original callback should resolve.
- AI cannot satisfy request; create staff task/notification.
- Duplicate Retell webhook for the inbound call.
- Same contact has multiple unresolved callback requests.

## Risks

- Poor callback-time extraction can create calls at the wrong time.
- Automating callbacks without clear clinic opt-in can surprise staff and patients.
- Over-sharing inbound call context with the outbound agent can violate minimum-necessary PHI.
- Race conditions between staff manual action and scheduled automation can duplicate follow-up.

## Validation Strategy

- Unit tests for callback automation setting decisions.
- Unit tests for preferred callback time normalization and past-time handling.
- Integration test for post-call callback classification creating exactly one workflow link.
- Integration test for manual resolution cancelling queued automation.
- RLS tests for callback workflow links.
- End-to-end staging scenario: inbound callback request -> workflow enrollment -> wait -> outbound call -> original callback resolved.

## Deployment Considerations

- Default every location to `manual` mode.
- Release configuration UI and read-only automation status before enabling outbound automation.
- Gate AI callback handling behind a per-location feature flag.
- Add metrics for callback enrollments, automated resolutions, fallback-to-staff, stale queued callbacks, and duplicate-prevention hits.
- Add runbook for disabling automation quickly per location.

## Future Extensibility

- Staff approval queue before AI callback.
- Callback request categorization to choose different workflows.
- Patient self-scheduling link fallback.
- Patient-level timezone support.
- SLA reporting for manual vs AI callback completion time.
