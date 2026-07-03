# Part 9 - Integration And Data Layer Implementation Plan

## What Needs To Be Built

Build the data foundation for proactive outbound campaigns while preserving live PMS access at action time. The system needs a thin, disposable, event-driven NexHealth read model for trigger discovery, plus paced recall-list ingestion, webhook subscription lifecycle management, idempotent event processing, reconciliation, and live revalidation before any outreach or booking action.

This does not mean syncing a full patient or appointment database. The read model exists only to decide who may be enrolled and when.

## Existing System Context

The backend already has:

- NexHealth adapter for live patient lookup, slot search, booking, cancellation, and rescheduling.
- Existing reference-data sync for providers, appointment types, operatories, and descriptors.
- NexHealth token management and Redis-backed rate limiting.
- `InstitutionLocation.nexhealth_subdomain` and `nexhealth_location_id` as PMS binding.
- Dormant `Institution.nexhealth_api_key_encrypted` for future per-tenant credential model.
- EventBridge -> Fargate scheduled jobs for periodic admin tasks.
- Signed webhook/idempotency patterns for Retell and Twilio.
- RLS-enforced tenant isolation.

Current gaps:

- No NexHealth webhook receiver for appointment events.
- No local appointment working set.
- No recall eligibility working set.
- No webhook subscription lifecycle per location.
- No reconciliation/backfill job for dropped or go-forward-only webhooks.
- Multi-key NexHealth support is partially prepared but unsafe until token cache/client pooling are keyed by API key.

## Existing Components To Reuse

- `NexHealthAdapter` and `NexHealthClient` for live API access.
- `NexHealthRateLimiter`, expanded/keyed correctly if multi-key is adopted.
- `TokenManager`, after cache/lock keys include API-key hash.
- `SyncService` patterns for per-location PMS reads and upserts.
- Dead-letter service for malformed or failed webhook events.
- Scheduled job harness and CDK pattern from `docs/SCHEDULED_JOBS.md`.
- Audit logging for PMS reads and configuration changes.

## New Components Required

### Data Model

- `nexhealth_webhook_subscriptions`
  - `institution_id`, `location_id`
  - `subdomain`, `nexhealth_location_id`
  - event types subscribed
  - provider subscription id
  - status: `active`, `pending`, `disabled`, `failed`
  - last health check, last event at, last backfill at
  - error metadata

- `nexhealth_webhook_events`
  - provider event id or deterministic hash
  - event type
  - `institution_id`, `location_id`
  - raw payload encrypted with retention window if needed
  - redacted payload
  - status: `processing`, `completed`, `failed`
  - attempts and last error
  - unique idempotency key

- `appointment_working_set`
  - `institution_id`, `location_id`
  - NexHealth appointment id
  - patient id reference/hash where needed
  - appointment start/end time
  - provider id, operatory id, appointment type id
  - status/cancelled/confirmed fields needed for eligibility
  - source updated timestamp/version if available
  - last webhook event at, last reconciled at
  - minimal contact hints only if required and encrypted

- `recall_eligibility_working_set`
  - `institution_id`, `location_id`
  - NexHealth patient id
  - recall type id/name
  - due date
  - eligibility status
  - last pulled at
  - expiration timestamp

- `pms_integration_credentials` or extend `institutions`
  - only if tenant/DSO-owned NexHealth keys are adopted
  - encrypted API key, key hash, status, owner scope

### Services

- `NexHealthWebhookService`
  - verifies NexHealth signatures
  - resolves location from payload/subscription
  - claims idempotency
  - dispatches event processors

- `AppointmentProjectionService`
  - upserts/deletes/marks inactive appointment working-set rows
  - treats cancellations as status updates
  - handles out-of-order events by using source timestamps where available

- `RecallEligibilityService`
  - paced off-peak pulls from NexHealth recall endpoints
  - derives overdue from due date
  - supports PMS capability fallback when recall data is unavailable

- `NexHealthSubscriptionService`
  - creates/updates/disables subscriptions per location
  - tracks subscription health
  - re-subscribes and triggers backfill after deactivation/outage

- `NexHealthReconciliationService`
  - initial backfill for upcoming appointments
  - low-frequency repair sweep
  - paced and jittered to protect shared partner limits

- `PmsLiveRevalidationService`
  - rechecks appointment/patient state at send time before workflow dispatch
  - ensures cancelled/rescheduled/confirmed appointments are skipped
  - **must run inside the paced send loop, not all upfront (Finding 13).** An 800-patient 9 AM
    reminder batch means ~800 live NexHealth calls at the burst moment against the ~1,000/min
    per-key budget — partly offsetting the read model's "fewer calls than polling" benefit. Trust
    a recent-webhook **freshness window** to skip redundant re-validation, and share one per-key
    budget view with the Part 1 scheduler's send-time pacing so background reconciliation and
    burst revalidation don't collectively exhaust the limit.

## End-To-End Implementation Approach

1. Add projection and webhook tables with RLS.
2. Add NexHealth webhook route and signature verification.
3. Add webhook idempotency claim pattern matching Retell's hot-path design.
4. Implement appointment projection updates for create/update/requested events.
5. Add subscription lifecycle service and admin/operator setup status.
6. Add initial backfill script/job for a location when subscription is created.
7. Add paced reconciliation scheduled job using existing EventBridge/Fargate pattern.
8. Add recall eligibility pull scheduled job, off-peak and paced per location.
9. Add trigger providers for workflow engine:
   - appointment time-offset from `appointment_working_set`
   - recall eligibility from `recall_eligibility_working_set`
10. Add live revalidation before enrollment dispatch and before send actions.
11. Add observability: stale projection, subscription disabled, webhook failures, reconciliation drift.

## Architecture Decisions

- Keep the PMS as system of record. Projections are disposable and can be rebuilt.
- Store only fields required for eligibility and scheduling. Do not create a full appointment/patient mirror.
- Process webhooks idempotently and asynchronously. Vendor webhook endpoints should verify, claim, enqueue, and return quickly.
- Use existing live NexHealth adapter for action-time booking/lookup.
- Use paced scheduled reconciliation rather than polling as the primary mechanism.
- Treat multi-key NexHealth routing as a contained extension, but do not rely on it for launch unless vendor confirms independent budgets.

## Technical Considerations

- NexHealth appointment cancellations arrive as updates, so status evaluation must happen on every update event.
- Webhook ordering is not guaranteed; processors must be monotonic where source timestamps allow it.
- Webhooks are go-forward only; every subscription activation needs initial REST backfill.
- NexHealth can deactivate webhook delivery after sustained failures; health checks must detect stale event streams.
- Current token cache key is global (`nh:token`) and must be keyed by API-key hash before any tenant-key routing.
- Current adapter uses a process-level singleton client. Multi-key routing needs a bounded keyed client pool.
- Current `NexHealthAdapter.create` hardcodes global settings API key. It must prefer decrypted tenant/DSO key only after token cache and pooling are fixed.

## Dependencies

- Workflow engine trigger model.
- NexHealth webhook subscription capability and exact payload/signature details.
- Scheduled job/CDK additions.
- Tenant-owned NexHealth key decision.
- Appointment/recall capability validation per PMS from `docs/Supported_API_Per_PMS_Nexhealth`.

## Edge Cases

- Webhook arrives for unknown location/subdomain.
- Duplicate webhook event.
- Update arrives before create.
- Appointment deleted/cancelled/rescheduled after enrollment but before send.
- Patient contact details changed in PMS after projection was created.
- Recall endpoint unsupported for a clinic's PMS.
- Recall patient has due date but no reachable contact method.
- Reconciliation discovers appointments missing from projection.
- Shared NexHealth rate limit exhausted during backfill.
- Tenant-specific API key revoked or returns auth failures.

## Risks

- Over-modeling appointments can drift toward a full PMS replica and increase PHI exposure.
- Under-modeling can make triggers unreliable if required fields are missing.
- Webhook downtime can silently stop campaign enrollments without strong health metrics.
- Multi-key NexHealth model is vendor-unconfirmed and may not deliver independent rate budgets.
- Backfills across many clinics can exceed shared rate limits without pacing and jitter.

## Validation Strategy

- Unit tests for appointment projection upsert/status/cancellation handling.
- Unit tests for webhook idempotency and out-of-order event behavior.
- Unit tests for recall overdue derivation from due dates.
- Integration tests for RLS on projection tables.
- Integration tests for initial backfill idempotency.
- Integration tests for reconciliation repairing missing/stale rows.
- Rate-limit tests proving paced jobs respect configured budgets.
- Manual staging test with a NexHealth test location: subscribe, backfill, receive update, enroll workflow trigger, revalidate live.

## Deployment Considerations

- Ship tables and read-only subscription status first.
- Add webhook route and signature verification before creating subscriptions.
- Enable one staging location, then one production pilot location.
- Add scheduled jobs using existing harness and CDK pattern.
- Add CloudWatch alarms for stale subscription, failed webhook processing, reconciliation failures, and rate-limit exhaustion.
- Keep a runbook for re-subscribe + backfill after webhook deactivation.

## Future Extensibility

- Tenant/DSO-owned NexHealth key routing if vendor confirms model.
- Expanded PMS data families: treatment plans, procedures, alerts, documents, financials.
- PMS sync-status monitoring.
- Self-serve integration setup and health dashboard.
- Patient timezone enrichment if source data supports it.
