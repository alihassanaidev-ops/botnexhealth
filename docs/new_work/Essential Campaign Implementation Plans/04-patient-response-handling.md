# Essential 4 - Patient Response Handling Implementation Plan

## What Needs To Be Built

Make patient responses first-class campaign events. SMS replies, voice outcomes, email clicks, booking confirmations, and staff handoffs should update workflow context, resume waiting runs, create tasks when automation cannot resolve the response, and feed campaign analytics.

The v1 goal is deterministic handling for common dental responses:

- YES/CONFIRM confirms appointment where supported.
- RESCHEDULE/CANCEL routes to booking/rescheduling or staff handoff.
- STOP/HELP follow compliance behavior.
- Free text becomes a staff follow-up item.
- Voice outcomes branch on booked, callback requested, transferred, voicemail, no answer, busy, failed, and do-not-call.
- Email clicks and booking links produce attributed response events.

## Existing System Context

The backend already has:

- `InboundSmsRoutingService` that records inbound SMS and best-effort correlates to one waiting run.
- SMS compliance primitives for STOP/HELP/suppression.
- Voice send nodes with `wait_for_outcome`.
- Retell/voice attempt recorder and voice outcome helpers.
- Workflow condition nodes that can branch on context fields.
- Callback queue and notification patterns.

Current gap:

- Free-text response handling is intentionally v1-basic.
- There is no unified patient-response event model.
- Staff handoffs are not tied into campaign operations and analytics as a product concept.
- Frontend does not expose response handling as a clear workflow concept.

## Existing Components To Reuse

- Inbound SMS webhook route and routing service.
- SMS suppression/consent services.
- Workflow waiting/resume logic.
- Retell webhook and voice attempt recorder.
- Notifications/task patterns.
- Campaign run detail and operations UI.

## New Components Required

### Data Model

- `campaign_response_events`
  - `institution_id`, `location_id`, `workflow_id`, `workflow_run_id`, `contact_id`
  - channel: `sms`, `voice`, `email`, `booking_link`, `staff`
  - normalized intent/outcome
  - raw vendor reference
  - confidence/source
  - PHI-safe summary and encrypted raw payload/body where required
  - timestamps

- `campaign_staff_handoffs`
  - linked response event/run/contact
  - reason: free_text, reschedule_requested, clinical_question, automation_failed, ambiguous_response
  - status: open, assigned, resolved, dismissed
  - assignee, due date, resolution outcome

### Services

- `CampaignResponseService`
  - normalize responses from SMS, voice, email, booking links
  - update run context
  - resume waiting workflow steps when deterministic
  - create handoff when not deterministic

- `SmsIntentParser`
  - deterministic keyword parser for YES/CONFIRM/C/RESCHEDULE/R/CANCEL/STOP/HELP
  - no generative NLU in v1

- `VoiceOutcomeMapper`
  - maps Retell outcomes to campaign response events and workflow context

- `EmailResponseAttributionService`
  - records delivered/opened/clicked/bounced/unsubscribed events where provider data exists
  - maps confirmation/booking link clicks to response events

## End-To-End Implementation Approach

1. Add unified response-event and handoff tables with RLS.
2. Implement deterministic SMS keyword parser.
3. Update inbound SMS route to call `CampaignResponseService` after recording the inbound message.
4. For appointment confirmation keywords, update workflow context and attempt PMS confirmation write-back where capability allows.
5. For reschedule/cancel/free text, create staff handoff and update run outcome/context.
6. Update Retell voice webhook path to emit response events and resume waiting runs when `wait_for_outcome` is enabled.
7. Add email event ingestion/attribution for delivered/open/click/bounce/unsubscribe where provider webhooks exist.
8. Add run timeline integration for response events and handoffs.
9. Add campaign analytics rollup inputs from response events.
10. Add frontend response/handoff views in campaign run detail and operations.

## Timeline

Estimated duration: 3 weeks.

- Days 1-3: response-event and handoff models, migrations, and service contract.
- Days 4-6: SMS keyword parser, inbound SMS integration, workflow resume behavior.
- Days 7-9: voice outcome mapping and `wait_for_outcome` branches.
- Days 10-12: handoff queue UI and run timeline integration.
- Days 13-15: email/link attribution, analytics event feed, and staging QA.

## Architecture Decisions

- Use deterministic parsing for v1 SMS responses. Generative classification can come later behind review.
- Store response events as the normalized audit trail; do not infer analytics directly from raw vendor rows.
- Create handoffs instead of guessing when a response is ambiguous.
- Keep STOP/HELP behavior in the compliance layer, but also mirror normalized events into campaign response analytics.

## Technical Considerations

- A phone number can map to multiple contacts. Only resume a run when the match is unambiguous.
- A patient can reply after the workflow has already exited. Record the response and create a handoff if needed.
- Appointment confirmation write-back depends on NexHealth/PMS support. The campaign outcome can still be `confirmed_by_reply` even if PMS write-back fails.
- Email open tracking is noisy. Treat clicks/bookings as stronger signals than opens.
- Voice outcome field names must be pinned to the actual Retell payload fields currently used by the app.

## Dependencies

- Campaign overview/run timeline plan.
- Basic analytics plan.
- Rich merge fields for confirmation/reschedule links.
- NexHealth data-flow plan for appointment write-back/revalidation.
- Staff notification/task patterns.

## Edge Cases

- Patient replies YES to the wrong campaign because multiple waiting runs exist.
- Patient texts STOP after also texting YES.
- Patient asks a clinical question in free text.
- Voice call books an appointment after SMS timeout already advanced the run.
- Email click is performed by a security scanner.
- Handoff is resolved manually outside the campaign UI.

## Risks

- Incorrectly confirming, cancelling, or rescheduling an appointment.
- Free-text automation overreach in a healthcare context.
- Duplicate response events from vendor retries.
- Analytics double-counting responses across channels.

## Validation Strategy

- Unit tests for SMS parser keyword variants and non-keyword free text.
- Integration tests for inbound SMS -> response event -> run context -> resume.
- Integration tests for ambiguous phone/contact matching.
- Voice outcome mapping tests with Retell fixture payloads.
- Handoff lifecycle API/UI tests.
- Rollup tests proving response events feed analytics once.

## Deployment Considerations

- Ship SMS response events and handoffs before automated PMS write-back.
- Gate PMS confirmation write-back by capability and feature flag.
- Show handoffs in operations as read-only first, then add assignment/resolution.
- Add monitoring for unresolved handoffs older than SLA.

## Future Extensibility

- NLU-assisted intent classification with confidence thresholds and human review.
- Two-way conversation inbox.
- Automated reschedule flow over SMS.
- Staff productivity metrics for handoffs.
