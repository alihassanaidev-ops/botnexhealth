# Essential 8 - Callback Trigger And Voice Outcome UI Exposure Implementation Plan

## What Needs To Be Built

Expose the backend-supported callback trigger and voice outcome settings in the campaign builder and campaign operations UI.

Clinic admins should be able to create a callback automation campaign, configure an outbound AI voice step to wait for the Retell outcome, branch on that outcome, and see callback/voice outcomes in run timelines and analytics.

## Existing System Context

The backend already has:

- `CallbackRequestedTrigger` in the workflow schema.
- `CallbackTriggerService`.
- Callback workflow enrollment task behavior.
- `SendVoiceNode.wait_for_outcome`.
- Voice attempt recorder and outcome helpers.
- Callback queue and call classification flow.

The frontend already has:

- Workflow node palette.
- Voice node configuration panel.
- Trigger catalog for appointment/recall/manual/bulk.
- Campaign detail with run outcomes.

Current gap:

- `callback_requested` is not fully exposed in the frontend trigger catalog.
- Voice `wait_for_outcome` and Retell outcome branching are not presented as a guided product flow.
- Callback automation lacks a first-class template and readiness checks.

## Existing Components To Reuse

- Workflow schema and backend validation.
- Callback queue pages/services.
- Voice profile/provisioning status.
- Workflow palette and step config panel.
- Campaign templates.
- Run timeline and response-event model.

## New Components Required

### Frontend

- Add `callback_requested` to `TriggerType`, trigger metadata, forms, summaries, and validation.
- Voice node config:
  - voice profile/Retell agent selector
  - `wait_for_outcome` toggle
  - max attempts
  - staff fallback behavior

- Voice outcome branch helper:
  - creates a condition node prefilled with `call_outcome`
  - suggested values: booked, callback_requested, transferred, voicemail, no_answer, busy, failed, do_not_call

- Callback automation template card and guided setup.

### Backend

- Confirm catalog/API responses include callback trigger.
- Add launch checklist items:
  - voice profile ready
  - callback queue source available
  - staff fallback configured
  - quiet hours respected

- Add response-event mapping for voice outcomes.
- Add analytics labels for callback automation:
  - callbacks automated
  - booked
  - transferred
  - staff handoff
  - unreachable
  - do-not-call

## End-To-End Implementation Approach

1. Add `callback_requested` to frontend workflow types and trigger catalog.
2. Update trigger editor and workflow graph rendering to support callback trigger.
3. Add voice node UI for `wait_for_outcome` and selected voice profile.
4. Add one-click branch helper for common voice outcomes.
5. Add callback automation template with manual staff fallback.
6. Extend launch checklist for callback/voice readiness.
7. Feed voice outcomes into response events and run timeline.
8. Add campaign analytics outcome mapping for callback workflows.
9. Add tests for builder rendering, validation, API payloads, and run/timeline display.

## Timeline

Estimated duration: 1.5 weeks.

- Days 1-2: frontend trigger type/catalog exposure and builder validation.
- Days 3-4: voice node outcome UI and branch helper.
- Days 5-6: callback template and launch checklist integration.
- Days 7-8: timeline/analytics outcome exposure, tests, and staging QA.

## Architecture Decisions

- Callback automation remains opt-in by activating a workflow with `callback_requested`.
- Voice outcomes become normalized response events, not one-off Retell-only fields.
- Builder should offer common outcome branches but still allow advanced condition editing.
- Keep staff handoff as the default fallback for ambiguous or failed voice outcomes.

## Technical Considerations

- Retell outcome field names must match the app's actual webhook mapping.
- `wait_for_outcome` can leave runs waiting if vendor webhook fails. Operations UI needs stale-wait detection.
- Voice provisioning is location-specific and must be checked before launch.
- Callback requested time should respect quiet hours and operating hours.
- A callback row may be manually handled before the automation fires.

## Dependencies

- Patient response handling.
- Campaign overview/run progress timeline.
- Dental-specific callback template.
- Launch checklist.
- Voice provisioning/readiness data.

## Edge Cases

- Multiple active callback workflows exist.
- Callback patient has no callable number.
- Patient requested callback time is in the past.
- Voice call outcome arrives twice.
- Staff manually resolves callback before workflow starts.
- Voice agent transfers to staff but no staff is available.

## Risks

- Callback automation calls patients outside acceptable hours.
- Voice outcome waiting runs get stuck without visibility.
- Multiple workflows double-enroll the same callback.
- Retell outcome labels drift from condition values shown in UI.

## Validation Strategy

- Frontend tests for callback trigger selection and graph summary.
- Frontend tests for voice node `wait_for_outcome` editing.
- Backend tests for callback trigger schema and workflow validation.
- Integration test for callback classification -> workflow enrollment -> voice wait -> outcome resume.
- Manual staging test with simulated Retell outcomes.

## Deployment Considerations

- Ship callback trigger exposure behind the campaign-builder feature flag.
- Enable template only for locations with voice provisioning.
- Add monitoring for runs waiting on voice outcome longer than threshold.
- Add operator runbook for callback workflow misconfiguration.

## Future Extensibility

- Multiple callback routing policies by call reason.
- Appointment-specific callback scripts.
- Callback SLA dashboards.
- AI summary of callback outcome for staff.
