# Campaign Implementation Decisions

Date: 2026-07-18

This file records the pre-implementation decisions for the essential campaign workflow build so product, engineering, and leadership stay aligned before implementation starts.

## Decisions

### 1. NexHealth API Version

Decision:

- Migrate the NexHealth integration to v3/v20240412 as part of this implementation.
- Campaign data ingestion, backfills, and webhook subscriptions should target the v3 API shape.

Reason:

- Current NexHealth docs and webhook subscription docs are v3/v20240412.
- Building campaign ingestion on v3 avoids implementing new campaign data flows against an older API shape and then reworking them.
- This adds pagination, endpoint, payload-shape, and subscription-versioning work to the campaign scope.

### 2. PMS Coverage

Decision:

- Build the campaign framework so it can support every PMS.
- For PMS-specific resources, use capability checks and feature gating.

Important clarification:

- Appointment/patient workflows should work broadly.
- Recall, treatment-plan, procedure, and insurance features depend on whether NexHealth supports that resource for the clinic's PMS.
- If a PMS does not support a resource, the UI should hide or disable that feature with an explanation instead of failing at runtime.

### 3. First Implementation Scope

Decision:

- Include appointment/callback campaigns.
- Include recall campaigns.
- Include treatment-plan follow-up campaigns.
- Include all three outbound channels: voice, SMS, and email.

Engineering note:

- Recall and treatment-plan features must still be capability-gated because NexHealth support varies by PMS.

### 4. Recall Scope

Decision:

- Recall campaigns are required in this implementation.

Implementation rule:

- NexHealth does not document recall-specific webhooks.
- Build recall support using scheduled `GET /patient_recalls` polling/backfill.
- Store recall data in a local `recall_working_set`.
- Enroll patients only after consent, suppression, future-appointment, and duplicate-run checks.

### 5. Treatment-Plan Scope

Decision:

- Treatment-plan follow-up is required in this implementation.

Implementation rule:

- Use `GET /treatment_plans` and treatment-plan webhooks where supported.
- Store minimal treatment-plan state in `treatment_plan_working_set`.
- Capability-gate per PMS.
- Do not expose sensitive treatment/procedure/fee details in patient-facing copy by default.

### 6. Outbound Channels

Decision:

- Voice, SMS, and email are all in scope.

Implementation rule:

- The workflow template defines the channel order.
- Runtime blocks unsafe/unavailable actions based on channel readiness, consent, suppression, quiet hours, frequency cap, and current PMS state.

### 7. Consent Source Of Truth

Decision:

- Our local consent, suppression, and do-not-contact tables are the source of truth.
- NexHealth `unsubscribe_sms`, where available, should be treated as an additional blocking hint.

Tables:

- `consent_records`
- `sms_suppressions`
- `do_not_contact`

### 8. Frequency Cap

Decision:

- Default cap: maximum 1 outbound campaign contact per patient per day.
- Default cap: maximum 3 outbound campaign contacts per patient per rolling 7 days across SMS, voice, and email.

Implementation note:

- Start by deriving from existing attempt tables if sufficient.
- Add `outbound_contact_attempts` or `contact_frequency_counters` if performance requires faster checks.

### 9. Quiet Hours

Decision:

- Use both legal-safe hours and clinic/local timezone.
- Recommended default: 9 AM to 6 PM local time unless clinic operating hours are stricter.

Implementation note:

- Use `location_operating_hours` and location timezone.
- If configured clinic hours are stricter, use the stricter window.

### 10. Appointment Confirmation Write-Back

Decision:

- Record the campaign outcome first.
- Write confirmation back to NexHealth/PMS only when endpoint/PMS support is confirmed.
- Keep PMS write-back behind a feature flag.

Reason:

- Confirmation write-back support can vary by PMS/API behavior.

### 11. Walk-Ins

Decision:

- Walk-ins should not trigger immediate pre-appointment reminder/confirmation campaigns.
- They may enter post-visit, review, recall, or treatment-plan follow-up after they are entered into PMS/NexHealth.

Implementation rule:

- If a walk-in is entered as a patient/appointment, normal patient/appointment events apply.
- If the clinic does not enter the walk-in into PMS/NexHealth, NexHealth provides no event.

### 12. AI Voice Booking/Rescheduling

Decision:

- AI voice is allowed to book and reschedule appointments.

Implementation rule:

- Use the existing booking/rescheduling flow through NexHealth.
- Revalidate patient, appointment, provider, slot, consent, and campaign state before action.
- If booking/rescheduling fails or is ambiguous, create staff handoff.

### 13. Staff Handoff

Decision:

- Use the existing callback queue initially.
- Add campaign handoff metadata/reason.
- Later, build a dedicated campaign handoff queue if volume requires it.

Handoff reasons:

- free-text reply
- reschedule request
- clinical question
- billing question
- failed booking
- ambiguous voice outcome
- patient asks for staff

### 14. Audience Preview

Decision:

- Audience preview is required for manual, bulk, recall, and treatment-plan campaigns.
- Appointment-triggered campaigns do not require full audience preview, but launch checklist should still show data freshness and expected volume where possible.

### 15. Manual Enrollment

Decision:

- Staff can select contacts manually.
- The system must block enrollment if consent, suppression, DNC, location, or duplicate-run checks fail.

### 16. Webhook Subscriptions

Decision:

- First implementation should subscribe to:
  - appointment webhooks
  - patient webhooks
  - sync-status webhooks

- Procedure/treatment-plan webhooks should be added where supported and needed for treatment-plan follow-up.

Implementation note:

- Keep webhook processing capability-gated by PMS/resource support.

### 17. Historical Backfill Windows

Decision:

- Upcoming appointments: 90 days.
- Patients: updated in last 12 months when needed for audience/contact matching.
- Recalls: due from 12 months past to 12 months future when recall is enabled.
- Treatment plans: updated in last 12 months when treatment-plan campaigns are enabled.

### 18. Raw Webhook Payload Retention

Decision:

- Always store normalized fields.
- Store encrypted raw webhook payloads only short-term for debugging, recommended 7-14 days.
- Redact or avoid raw PHI in logs.

### 19. Analytics V1

Decision:

Track these first:

- sent
- delivered
- failed
- answered
- no-answer
- voicemail
- confirmed
- booked
- handoff
- opt-out
- cost

### 20. Feature Gating

Decision:

- Hide unsupported features from normal clinic users.
- Show disabled features with explanation to admins/operators.

Examples:

- If PMS does not support `patient_recalls`, disable recall campaign creation.
- If PMS does not support treatment plans, disable treatment-plan follow-up templates.
- If voice is not provisioned, disable voice steps or block launch.

## Final Scope Statement

The implementation will build a multi-channel campaign framework for appointment, callback, recall, and treatment-plan workflows.

Appointment and callback workflows are broadly safe.

Recall and treatment-plan workflows are required, but they must be implemented with PMS capability checks because NexHealth support varies by PMS.

The system must never assume a PMS supports a resource just because the campaign feature exists.
