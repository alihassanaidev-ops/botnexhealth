# Part 4 - Outbound SMS Implementation Plan

## What Needs To Be Built

Build sequenced and bulk outbound SMS as a workflow action. The system must send compliant campaign texts from the correct clinic sender, honor STOP/START and do-not-contact suppression, track delivery, support quiet-hours scheduling, and feed patient replies or delivery outcomes back into workflow runs.

This extends the current one-off and call-triggered SMS capability into a campaign/runtime capability.

## Existing System Context

The backend already has:

- `SmsService` for Twilio sends and encrypted `sms_history_logs`.
- `SmsComplianceService` for SMS consent, suppression, and do-not-contact checks.
- Twilio inbound SMS webhook for STOP/START/HELP in `twilio_webhooks.py`.
- Twilio delivery-status webhook updating `sms_history_logs`.
- `enqueue_auto_sms` and Celery task retries/dead-letter capture.
- Location sender enforcement using `InstitutionLocation.twilio_from_number`.
- PHI-safe logging, masked phone values, encrypted body/recipient fields, and retention logic.

Current gaps:

- SMS is one-off, not tied to workflow runs or campaign steps.
- Consent model currently only has `sms` and institution-level active suppression behavior; scope requires location/channel-aware cross-channel suppression.
- No quiet-hours/send-window hold exists today.
- Inbound non-keyword replies are ignored instead of creating staff notifications or workflow events.
- Twilio credentials are platform-level, not per-clinic sub-account.

## Existing Components To Reuse

- `SmsService.send_sms(...)` as the low-level delivery/logging path, after it is adapted for tenant credentials.
- `SmsComplianceService` as the base compliance gate.
- `SmsHistoryLog` as the canonical SMS delivery record, extended with campaign linkage.
- Twilio webhook verification and STOP/START/HELP parsing.
- Dead-letter and retry classification.
- SSE event bus for workflow/campaign updates.
- Existing notification service to alert staff for free-text replies in v1.

## New Components Required

### Data Model

- **Consent/suppression schema is owned by Part 12 (Compliance & Consent), not this plan.**
  The multi-channel consent migration (extend `ConsentChannel` + CHECK to `sms`/`voice`/`email`),
  location-scoped suppression uniqueness, and institution/group-wide do-not-contact are defined
  **once** in Part 12 so SMS and Email (Part 5) do not migrate the same tables in conflicting
  ways. This plan **consumes** `ComplianceGateService`/`SuppressionService` from Part 12; it does
  not create or alter consent tables. (Existing STOP/START keyword handling stays here as the
  SMS-specific signal that feeds Part 12's suppression.)

- Extend `sms_history_logs`:
  - `workflow_run_id`
  - `workflow_step_id`
  - `campaign_id` or `workflow_id`
  - `attempt_number`
  - `template_id` or rendered-template hash
  - `provider_segments`
  - `price_amount`, `price_currency` when Twilio provides it

- Add `workflow_sms_attempts` if SMS attempt state needs to be separate from delivery log:
  - unique idempotency key on `(workflow_run_id, workflow_step_id, attempt_number)`
  - status, block reason, Twilio SID, delivery timestamps

- Add `inbound_sms_messages`:
  - `institution_id`, `location_id`, `contact_id`
  - Twilio message SID
  - from/to hash and masked phone
  - encrypted body
  - classified intent: `stop`, `start`, `help`, `free_text`
  - linked `workflow_run_id` if correlation is possible

### Services

- `WorkflowSmsActionService`
  - renders approved templates with merge fields
  - enforces compliance by calling Part 12 `ComplianceGateService` (consent, suppression, quiet
    hours, **frequency cap**), plus max attempts and idempotency
  - calls `SmsService` or a credential-aware variant
  - updates workflow attempt state

- `QuietHoursService` — **defined in Part 1** (durable engine) and invoked via Part 12's gate;
  listed here only as a dependency. Not re-implemented in this plan. Evaluates
  `InstitutionLocation.timezone` and returns dispatch-now vs next allowed local time.

- `InboundSmsRoutingService`
  - handles free-text inbound replies
  - correlates by sender/recipient number and recent open workflow runs
  - in v1, creates staff notifications rather than autonomous NLU conversation
  - emits workflow event only for explicit keywords/structured responses that templates define

- `SmsTemplateRenderer`
  - uses a strict approved merge-field allowlist
  - produces preview and final render
  - rejects PHI fields not approved for the campaign/content class

## End-To-End Implementation Approach

1. Add workflow linkage fields to SMS delivery records.
2. Generalize consent/suppression scope to location-aware rules without breaking existing STOP behavior.
3. Add quiet-hours service and scheduler handoff contract.
4. Add SMS workflow action service with idempotent attempt creation.
5. Update Twilio status webhook to notify workflow attempts when delivery changes.
6. Update inbound SMS webhook to persist free-text replies and notify staff.
7. Add workflow/campaign SSE event types after event bus schema is extended.
8. Add CSV/bulk enrollment validation to require reachable phone and consent provenance before SMS steps execute.
9. Add usage metering from segments/status callbacks.

## Architecture Decisions

- Keep `SmsHistoryLog` as the immutable delivery audit trail. Workflow attempt rows can reference it, but should not replace it.
- Enforce compliance in the workflow action service before calling `SmsService`, and keep `SmsService`'s own compliance check as a defense-in-depth gate.
- Treat STOP as location/sender-scoped by default, with a privileged path for institution-wide or group-wide do-not-contact.
- For v1, free-text replies create staff notifications and optionally pause the run. Do not build a conversational SMS agent until explicitly scoped.
- Keep Twilio webhook payloads PHI-redacted in dead letters, following existing `redact_payload` patterns.

## Technical Considerations

- Existing enum/check constraints only allow `ConsentChannel.SMS`; expanding to email/voice requires migrations and careful compatibility.
- Existing suppression unique index is institution+channel+phone. Scope requires sender/location-specific handling; migration must preserve old rows and interpret them safely.
- The Twilio client currently uses platform credentials. Per-clinic sub-accounts require credential resolution by location before send and signature validation for webhooks across sub-accounts.
- SMS bodies are PHI-bearing. Keep encryption and retention policies, and avoid putting full rendered text in workflow state JSON.
- Quiet-hours holds belong in the durable workflow scheduler, not Celery `countdown`.
- Delivery callbacks can arrive after a workflow run has already ended; handle as a delivery-log update plus metrics event, not necessarily a state transition.

## Dependencies

- Workflow engine and durable scheduler (Part 1).
- **Part 12 compliance/consent layer** (consent schema, `ComplianceGateService`, suppression, frequency cap).
- Per-tenant Twilio provisioning and credential lookup (Part 10).
- Quiet-hours/send-window model (Part 1).
- Template/merge-field validation.
- Campaign management UI for copy, retries, and channel selection.
- Usage metering (Part 11).

## Edge Cases

- STOP received while a run has future SMS/email/voice steps.
- START received after a campaign suppressed a run.
- Delivery callback for unknown Twilio SID.
- Inbound reply from a shared family phone with multiple contacts.
- Same contact enrolled in two campaigns with conflicting SMS timing.
- Message due outside send window.
- Template renders longer than expected and creates multiple segments.
- Twilio sub-account suspended or A2P campaign rejected.
- Patient replies with "stop" in mixed case, punctuation, or bilingual wording.
- Location has sender number but no per-tenant credential configured.

## Risks

- Retrofitting location-scoped suppression into existing institution-scoped tables can create subtle compliance regressions.
- Bulk/CSV enrollment can import numbers without sufficient consent proof.
- Per-clinic Twilio sub-accounts change webhook validation and status-callback routing.
- Campaign content may accidentally shift from care-related communication into marketing; validator must enforce content class.

## Validation Strategy

- Unit tests for consent/suppression precedence and location vs institution scope.
- Unit tests for quiet-hours next-send calculations, including DST.
- Unit tests for template merge-field allowlist.
- Existing Twilio signature tests extended for per-sub-account credentials.
- Integration tests for SMS attempt idempotency.
- Integration tests for STOP suppressing future workflow steps.
- RLS tests for SMS attempt and inbound message tables.
- End-to-end staging test with Twilio test credentials: send, status callback, reply, staff notification, workflow update.

## Deployment Considerations

- Migrate consent/suppression tables in a backward-compatible way.
- Keep current one-off SMS routes working during rollout.
- Introduce per-location credential selection behind a feature flag.
- Add metrics for suppressed sends, Twilio failures, delivery latency, inbound replies, and uncorrelated callbacks.
- Add operator runbooks for A2P/10DLC rejection, toll-free verification issues, and Twilio webhook failures.

## Future Extensibility

- Autonomous SMS conversation agent.
- MMS attachments if compliance and template controls allow it.
- Bilingual templates and keyword handling.
- *(A basic per-patient/per-provider frequency cap ships in v1 via Part 12 — see Finding 3;
  richer per-campaign fatigue policy and preference-center are the future extension here.)*
- Link tracking with PHI-safe redirects if marketing use cases are later approved.
