# Part 3 - Outbound Voice Calling Implementation Plan

## What Needs To Be Built

Build per-location outbound AI voice calling as a workflow action. The workflow engine will initiate Retell outbound calls, route each call through the correct clinic/location context, reuse the existing Retell function-call handlers for patient lookup, slot search, booking, cancellation, and rescheduling, and feed dial/call outcomes back into the workflow run.

This is not a replacement for the current inbound agent. It extends the same Retell -> backend -> NexHealth -> PMS path that already exists for inbound calls.

## Existing System Context

The backend already has:

- Retell function endpoint: `POST /api/v1/retell/functions` in `src/app/retell/functions.py`.
- Retell webhook endpoint: `POST /api/v1/retell/webhook` in `src/app/retell/webhooks.py`.
- Function registry and handlers in `src/app/retell/handlers.py`.
- Per-location Retell routing through `InstitutionLocation.retell_agent_id`.
- Post-call persistence through `PostCallService`, including encrypted transcript/summary, `Call.call_direction`, `Call.retell_call_id`, `Call.location_id`, and contact upsert.
- PMS booking safety through existing idempotency in `retell_function_invocations`.
- Location operating hours, breaks, provider buffers, and live NexHealth slot search.
- Celery workers, dead-letter capture, audit logging, and PHI-safe logging conventions.

The current limitation is that the repo only receives calls and webhooks. It does not initiate Retell calls, store outbound agent bindings separately from inbound, enforce outbound call concurrency, or connect a call attempt to a workflow run.

## Existing Components To Reuse

- `InstitutionLocation` as the execution context and tenant boundary.
- Existing Retell function dispatcher and handler registry.
- Existing PMS adapter factory and NexHealth booking flow.
- Existing post-call webhook ingestion and `PostCallService`.
- Existing `Call` model for call records, extended rather than duplicated.
- Existing audit service for PMS reads/writes and call actions.
- Existing dead-letter service for failed vendor calls.
- Existing SSE event bus for progress updates after workflow integration.
- Existing Celery worker patterns for async vendor work.

## New Components Required

### Data Model

Add outbound-specific tables/columns through Alembic:

- `outbound_voice_profiles`
  - `institution_id`, `location_id`
  - `retell_workspace_id` if available from Retell/BYO telephony model
  - `outbound_retell_agent_id`
  - `caller_number`
  - encrypted Retell workspace/API credential reference if credentials become tenant-specific
  - `max_concurrent_calls`
  - `is_active`
  - timestamps

- `workflow_voice_attempts`
  - `institution_id`, `location_id`
  - `workflow_run_id`, `workflow_step_id`
  - `contact_id`
  - `retell_call_id`
  - `to_number_hash`, `to_number_masked`, encrypted `to_number`
  - `from_number`
  - status: `queued`, `initiating`, `in_progress`, `completed`, `failed`, `suppressed`, `cancelled`
  - dial outcome: `answered`, `busy`, `no_answer`, `voicemail`, `failed`, `transferred`, `unknown`
  - terminal workflow outcome mapping
  - idempotency key unique on `(workflow_run_id, workflow_step_id, attempt_number)`
  - timestamps and retain/purge columns

- Extend `calls` only where needed:
  - optional `workflow_run_id`
  - optional `workflow_step_id`
  - optional `voice_attempt_id`
  - optional `dial_outcome`

Every tenant-scoped table must have `institution_id`, RLS enabled, and tests proving location users cannot cross-read attempts.

### Services

- `OutboundVoiceService`
  - validates location voice profile
  - enforces call permission and suppression gates
  - claims a concurrency slot
  - calls Retell outbound call API
  - records a voice attempt before the external side effect
  - updates the attempt with Retell call id and initial status

- `OutboundVoiceConcurrencyService`
  - per-location Redis semaphore for short-lived active calls
  - DB-backed reconciliation for leaked slots after worker crashes
  - limits configured on `outbound_voice_profiles.max_concurrent_calls`

- `RetellOutboundClient`
  - small HTTP client isolated from inbound webhook/function code
  - no PHI in logs
  - support timeout/retry policy aligned with existing vendor patterns

- Workflow action adapter
  - invoked by the workflow engine when an `ai_voice_call` step becomes due
  - receives run/step/contact/location context
  - dispatches via `OutboundVoiceService`
  - maps attempt result back into workflow state

### API/UI

- Admin/operator setup endpoints for outbound voice profile CRUD.
- Institution/location admin read-only validation status for outbound readiness.
- Campaign run drill-down should show outbound call attempts and outcomes.

## End-To-End Implementation Approach

1. Add outbound voice profile storage.
2. Add Retell outbound client behind an interface that can be mocked in tests.
3. Add `workflow_voice_attempts` with idempotency and RLS.
4. Implement `OutboundVoiceService.start_call(...)`.
5. Add per-location concurrency reservation and release.
6. Add workflow action integration once the workflow runtime exists.
7. Update Retell webhook processing to correlate `retell_call_id` back to `workflow_voice_attempts`.
8. Extend `PostCallService` to persist `workflow_run_id` linkage on the `Call` row when a matched attempt exists.
9. Publish workflow/campaign SSE events after attempt state changes.
10. Add operator dead-letter replay paths for failed call initiation.

## Architecture Decisions

- Use separate outbound agent/profile fields instead of overloading `retell_agent_id`. The current column is the inbound routing key. Outbound calls may require different prompts, workspace credentials, phone numbers, or concurrency rules.
- Keep Retell function handlers shared between inbound and outbound. Booking logic, identity gates, and PMS idempotency should remain one implementation.
- Correlate by Retell `call_id`, but claim workflow attempt idempotency before initiating the call. This avoids duplicate calls if a worker retries after a partial failure.
- Keep booking live against NexHealth. No appointment slots should be taken from local projections.
- Concurrency is per location, not global. The scope requires per-clinic isolation and Retell workspace sharding.

## Technical Considerations

- Retell outbound-call API details and per-workspace credential model must be confirmed against Retell's current API before implementation.
- If each clinic/DSO uses a separate Retell workspace, secrets should live encrypted per profile or in AWS Secrets Manager references, not plaintext config.
- Use the existing `hash_for_logging`, `safe_error_summary`, and encrypted model-property patterns for phone numbers and call metadata.
- Do not pass workflow identifiers to Retell in a way that exposes tenant internals to the patient. Use metadata fields only if Retell supports private metadata.
- Ensure outbound call webhooks use the same signature verification as inbound.
- A call initiation failure is not the same as a completed no-answer call. Workflow branching must distinguish vendor failure from patient outcome.
- If Retell returns `voicemail` only in post-call analysis, keep the attempt `in_progress` until webhook completion.
- **AI-voice legal requirements (Gap Analysis Finding 1) are enforced via Part 12.** The FCC
  (Feb 2024) treats AI-generated voice as "artificial voice" under the TCPA. Confirmation/Reminder
  to existing patients are exempt (opt-out suffices), but **Recall and Sales Qualification require
  a written/express consent basis** (checked by Part 12 before the call is placed). The outbound
  agent prompt **must include in-call identity disclosure + an opt-out path**, and an AI voicemail
  is itself an artificial-voice message under the same rules. Route the patient's spoken opt-out
  (Retell tag) into Part 12 suppression.
- **Cross-channel fallback (voicemail → SMS) crosses consent domains (Finding 14).** Voice consent
  ≠ SMS consent, and the dialed number may be a landline (SMS fails silently). Every fallback
  channel switch must re-check that channel's own consent + line-type through the Part 12 gate,
  not inherit voice consent.

## Dependencies

- Workflow engine data model and run/step concepts (Part 1).
- **Part 12 compliance/consent layer** — voice consent basis, in-call disclosure/opt-out, suppression, frequency cap.
- Contact model.
- Per-location outbound voice provisioning (Part 10).
- Retell API credentials and workspace strategy.
- Operations tooling for replay and failed attempts.
- Usage metering for call minutes and dials.

## Edge Cases

- Worker retries after Retell accepted the call but before DB update.
- Retell webhook arrives before attempt row is updated with `retell_call_id`.
- Call is answered and books an appointment, but webhook processing fails.
- Patient asks to opt out during a voice call.
- Patient reaches voicemail and the workflow should switch to SMS/email.
- Location has no outbound voice profile or disabled profile.
- Location has no PMS; booking tools must remain disabled through existing PMS guards.
- Concurrency slot leaked by a worker crash.
- Retell agent id cannot be resolved to a location.
- Multiple workflow runs try to call the same contact at once.

## Risks

- Retell outbound API/workspace/BYO SIP details may constrain the desired per-clinic model.
- Incorrect correlation can orphan calls from workflow runs.
- Over-aggressive retries can duplicate calls if idempotency is not claimed before initiation.
- Voice calls may trigger stricter consent requirements than current SMS-only consent tables cover.
- Per-clinic Retell workspaces increase onboarding and credential-management complexity.

## Validation Strategy

- Unit tests for outbound call idempotency and concurrency reservation/release.
- Unit tests for Retell client retry/error classification with mocked responses.
- Integration tests for RLS on `workflow_voice_attempts`.
- Integration tests for webhook correlation from Retell `call_id` to voice attempt and workflow run.
- Regression tests proving existing inbound Retell function handlers still work.
- Tests for no-PMS tenants returning graceful booking-disabled responses.
- Failure tests for dead-letter capture on Retell API outage.
- Manual staging test: one location, one outbound profile, one call attempt, one post-call webhook, one workflow transition.

## Deployment Considerations

- Add migrations before enabling outbound actions.
- Deploy profile tables and read-only setup status first.
- Add Retell outbound credentials as secrets and validate in staging.
- Roll out per location behind an `outbound_voice_enabled` flag.
- Start with low concurrency default, such as 1 active outbound call per location.
- Add CloudWatch metrics for call initiation failures, active calls, stale in-progress attempts, and webhook correlation misses.

## Future Extensibility

- Multiple outbound agents per location for different campaigns.
- Per-DSO Retell workspace profiles.
- Voice consent model beyond SMS consent.
- Call scoring and AI disposition extraction feeding campaign analytics.
- Budget/concurrency caps at institution and DSO group levels.
