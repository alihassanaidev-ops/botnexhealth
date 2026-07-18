# Task Plan: 04 Patient Response Handling

## Goal

Implement the Patient Response Handling plan after earlier plans are complete.

## Current Phase

Complete

## Phases

- **Status:** complete
- Read Plan 04, existing status/session files, and response-related decisions in Plans 09-12.
- Used graphify to locate inbound SMS routing, Retell voice outcome resume, callback/notification paths, and campaign operations/timeline code.
- Added normalized response-event and campaign staff handoff persistence with RLS.
- Added deterministic SMS response parsing and wired inbound Twilio SMS replies into response events/handoffs.
- Wired Retell voice outcome resume into response events.
- Added best-effort email response events for scoped unsubscribe and Resend suppression webhooks.
- Exposed response counts, open handoffs, response timeline items, and handoff operations in the campaign API/frontend.
- Verified with focused backend/frontend tests, lint, voice integration tests, and frontend build.

## Key Questions

1. How should bare `CANCEL` be interpreted?
   - Resolved conservatively in implementation: bare `CANCEL` remains SMS opt-out for compliance; appointment-specific cancellation phrases such as "cancel my appointment" become staff handoffs.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Keep v1 SMS parsing deterministic and keyword-only. | Plan 04/12 prohibit generative classification for patient replies in v1. |
| Preserve compliance handling for STOP/HELP/START and bare CANCEL opt-out. | Avoid weakening existing SMS compliance semantics. |
| Route appointment cancellation/reschedule/free text to staff handoffs, not automation. | Plan 04/12 require handoff instead of guessing for ambiguous or operationally risky patient intent. |
| Surface handoffs in campaign operations read-only. | Plan 12 says use initial handoff metadata/queue patterns before building a dedicated lifecycle UI. |
| Record scoped email provider events only when attribution data exists. | Resend bounce/complaint payloads can be unscoped; implementation avoids guessing campaign/run linkage. |
