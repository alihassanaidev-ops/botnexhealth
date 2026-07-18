# Findings And Decisions

## Requirements

- Patient responses must be normalized into campaign response events instead of inferred directly from raw vendor rows.
- SMS parsing must be deterministic for v1; no generative/NLU classifier.
- STOP/HELP compliance behavior remains in the compliance layer and is mirrored into response analytics.
- Ambiguous, free-text, reschedule, cancellation, clinical, billing, and staff-requested replies become staff handoffs.
- Appointment confirmation write-back remains gated by existing PMS capability checks.
- Voice outcomes should flow through existing Retell outcome mapping and workflow resume behavior.
- Email provider events can be recorded only where current webhook data provides safe attribution/scope.

## Research Findings

- Existing `InboundSmsRoutingService` already persists encrypted inbound SMS rows and correlates to one unambiguous waiting run.
- Existing Twilio route handled STOP/START/HELP, bare SMS confirmation replies, and generic free-text staff notifications.
- Existing `resume_sms_confirmation` resumes waiting confirmation workflows and performs PMS confirmation write-back only when capability support exists.
- Existing `resume_voice_outcome` resumes parked outbound voice runs by `retell_call_id` and branches on `call_outcome`.
- Existing campaign operations/timeline service was the right surface for response events and read-only handoffs.
- Existing callback queue is call-based; Plan 12 says to add campaign handoff metadata initially and build a dedicated queue later if volume requires it.
- Resend webhook events can lack institution tags, so unscoped email bounce/complaint events should not guess workflow attribution.
- Fresh Alembic upgrades failed when recording the existing 34-character Plan 03 revision ID into Alembic's default `varchar(32)` version table; widening the version table inside that migration fixes fresh upgrade.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Add `campaign_response_events` and `campaign_staff_handoffs` tables now. | Gives Plan 04 a normalized audit/analytics stream and an explicit human-review surface. |
| Store raw SMS body / raw payload encrypted, and expose only PHI-light summaries. | Patient replies and provider payloads may contain PHI. |
| Keep bare `CANCEL` as SMS opt-out, but route appointment-specific cancellation phrases to handoff. | Preserves compliance while satisfying Plan 04 cancellation-request handling. |
| Do not auto-cancel or auto-reschedule from SMS. | Plan 04/12 require staff handoff when automation cannot safely resolve the patient response. |
| Emit voice response events inside the existing voice resume task. | Reuses the established Retell correlation point and keeps branch behavior unchanged. |
| Add response/handoff timeline and operations rows instead of a new immediate handoff management UI. | Matches Plan 12's read-only-first handoff direction. |
| Record scoped email unsubscribe/bounce/complaint events best-effort; leave unscoped provider events to suppression tasks only. | Avoids incorrect campaign attribution when provider payloads lack institution/workflow scope. |
