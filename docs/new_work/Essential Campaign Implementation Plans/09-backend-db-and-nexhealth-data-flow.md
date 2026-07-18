# Essential 9 - Backend DB And NexHealth Data Flow Implementation Plan

## What Needs To Be Built

Define the backend data flow for campaign audiences, triggers, merge context, outcome attribution, and analytics across the local database and NexHealth.

The system should use NexHealth webhooks for upcoming data where available, REST backfill/reconciliation for historical and repair data, and live NexHealth revalidation before any patient outreach or PMS write action.

This plan answers two specific questions:

- Upcoming data: yes, NexHealth exposes webhooks for appointment, patient, procedure, treatment-plan, conversation/message, insurance, financial, and sync-status events.
- Historical data: yes, NexHealth exposes REST endpoints for patients, appointments, patient recalls, procedures, treatment plans, insurance plans, charges, sync status, and related resources. Historical/audience use cases must use paginated REST backfill with `updated_since`, date windows, cursor pagination, and PMS capability checks; webhooks are not a substitute for initial history.

## Research Summary

Sources checked on 2026-07-17:

- NexHealth current API introduction: https://docs.nexhealth.com/reference/introduction
- Webhook endpoints: https://docs.nexhealth.com/reference/webhook-endpoints
- Webhook subscriptions: https://docs.nexhealth.com/reference/webhook-subscriptions
- Patients endpoint: https://docs.nexhealth.com/reference/getpatients
- Appointments endpoint: https://docs.nexhealth.com/reference/getappointments
- Patient recalls endpoint: https://docs.nexhealth.com/reference/getpatientrecalls
- Procedures endpoint: https://docs.nexhealth.com/reference/getprocedures
- Treatment plans endpoint: https://docs.nexhealth.com/reference/gettreatmentplans
- Sync status endpoint: https://docs.nexhealth.com/reference/getsyncstatus
- v2 to stable migration guide: https://docs.nexhealth.com/docs/api-v2-to-v20240412-migration-guide

Important findings:

- Current NexHealth API major version is v3.0.0, also published as v20240412.
- NexHealth webhook endpoint registration requires HTTPS and returns a secret key for verifying incoming messages.
- NexHealth retries non-2xx webhook deliveries up to 48 hours and can deactivate the endpoint after sustained failures.
- Current webhook subscriptions include appointment, patient, patient insurance, procedure, procedure code, treatment plan, conversation, message, financial objects, onboarding, form/document, and sync-status event families.
- Sync-status webhooks indicate read/write recovery, but red/down transitions require polling `GET /sync_status` and checking status timestamps.
- `GET /patients` supports `location_id`, `updated_since`, `inactive`, `name`, `email`, `phone_number`, `foreign_id`, `new_patient`, `non_patient`, `location_strict`, and cursor pagination.
- `GET /appointments` supports time windows, `updated_since`, patient/provider/operatory filters, status filters, and cursor pagination.
- `GET /patient_recalls` supports `location_id`, `recall_id`, `patient_id`, `foreign_id`, `updated_since`, `due_after`, sorting by due date, and cursor pagination.
- Patient recalls are not universally supported; supported integrations listed in docs include Cloud9, Curve, Denticon, Dentrix, Dentrix Ascend, Dentrix Enterprise, Eaglesoft, OpenDental, Practiceworks, Orthotrac, NextGen, and Dolphin.
- Treatment plans are supported for Dentrix, Dentrix Enterprise, Eaglesoft, and Open Dental, with filters for `patient_id`, `status`, and `updated_since`.
- Procedures are supported for Dentrix, Dentrix Enterprise, Eaglesoft, and Open Dental, and require at least one filter beyond `location_id`.

## Existing System Context

The backend already has:

- `docs/NEXHEALTH.md` with integration caveats and rate limits.
- Stable API pinning in code using the v2 Accept header today.
- NexHealth adapter for live patient lookup, appointment lookup, slots, booking, cancellation, and rescheduling.
- Reference-data sync for providers, appointment types, operatories, and descriptors.
- Token manager and Redis-backed rate limiter.
- `nexhealth_webhook_subscriptions` model.
- `nexhealth_webhook_events` model.
- `appointment_working_set` model.
- NexHealth appointment webhook receiver.
- Appointment projection/backfill/reconciliation services.
- Live revalidation service.

Current gap:

- Appointment data flow exists, but the broader campaign data flow for patients, recalls, procedures, treatment plans, insurance, sync status, and analytics attribution is not fully planned or implemented.
- Current webhook receiver is appointment-specific.
- There is no unified PMS data capability registry consumed by campaign features.
- Historical backfill policy is not defined for campaign audiences.

## Existing Components To Reuse

- `NexHealthAdapter`, `NexHealthClient`, token manager, and rate limiter.
- `NexHealthWebhookSubscription` and event-ledger patterns.
- `AppointmentWorkingSet`.
- `NexHealthAppointmentSyncService`.
- `PmsLiveRevalidationService`.
- Existing reference-data sync.
- `docs/Supported_API_Per_PMS_Nexhealth/*` capability matrices.
- Scheduled job/EventBridge/Fargate patterns.
- RLS/session context patterns for NexHealth and Celery.

## New Components Required

### Data Model

- `pms_capability_matrix`
  - institution/location/PMS resource support snapshot
  - source: checked-in matrix plus live probe results where needed
  - capabilities: appointments, recalls, procedures, treatment_plans, insurance, charges, confirmation_writeback

- `patient_working_set`
  - minimal campaign-safe patient/contact projection
  - NexHealth patient ID, contact ID, inactive/new/non-patient flags, preferred language, guardian IDs where available
  - no full PMS patient mirror

- `recall_working_set`
  - patient ID, recall type, due date, updated timestamp, status/eligibility
  - expires/reconciles by location

- `procedure_working_set`
  - patient ID, appointment ID, procedure code/name/status/date/fee when campaign-safe
  - used for outcome attribution and future treatment/revenue analytics

- `treatment_plan_working_set`
  - patient ID, status, proposed/accepted/completed state, procedure summary, updated timestamp
  - only for supported PMSs

- `nexhealth_resource_events`
  - generalized webhook ledger beyond appointments
  - provider event ID or deterministic hash, event family, resource ID, status, attempts, redacted payload

- `pms_sync_watermarks`
  - per location/resource high-water marks for backfill/reconciliation
  - last success, last failure, cursor state if needed

### Services

- `NexHealthWebhookRouter`
  - one secure endpoint or family-specific endpoints
  - validates HMAC
  - normalizes event family and dispatches processors

- `PmsCapabilityService`
  - answers whether a location can support recall, treatment plan, procedure, confirmation write-back, etc.

- `PatientBackfillService`
  - cursor-paginated patient load using `updated_since`
  - minimal projection and contact linking

- `RecallBackfillService`
  - paced `GET /patient_recalls` by location, `updated_since`, `due_after`
  - stores recall eligibility and unsupported-PMS states

- `ProcedureBackfillService`
  - incremental procedure sync for supported PMSs
  - supports outcome attribution and future revenue reporting

- `TreatmentPlanBackfillService`
  - incremental treatment plan sync for supported PMSs
  - feeds unscheduled treatment follow-up campaigns

- `SyncStatusMonitor`
  - polls `GET /sync_status`
  - consumes sync-status recovery webhooks
  - marks data freshness and campaign launch readiness

## End-To-End Data Flow

### Upcoming Appointment Data

1. NexHealth appointment webhook arrives.
2. Signature is verified with endpoint secret.
3. Event is claimed in webhook ledger by deterministic dedup key.
4. Location is resolved by NexHealth location ID/subdomain.
5. `appointment_working_set` is upserted.
6. New/rescheduled appointments trigger matching active appointment-offset workflows.
7. Cancelled appointments cancel active reminder/confirmation runs.
8. Send-time revalidation checks freshness window and calls NexHealth live when needed.

### Historical Appointment Data

1. Initial subscription setup starts a REST backfill over a configured future window, for example 90 days.
2. Reconciliation job periodically scans upcoming appointments by location.
3. Drift repairs update `appointment_working_set`.
4. Reconciliation can re-enroll rescheduled/new appointments and cancel dead runs.

### Patient And Contact Data

1. Patient backfill uses `GET /patients` with location, cursor pagination, and `updated_since`.
2. The system stores only fields needed for matching, consent/channel availability, segmentation, preferred language, and merge context.
3. Contact remains the local communication entity; NexHealth patient ID links PMS identity to contact.
4. Patient webhooks update the working set and contact hints where safe.

### Recall Data

1. Capability check confirms `patient_recalls` support for the clinic's PMS.
2. Initial recall backfill pulls due and recently updated recalls by location.
3. `recall_working_set` drives recall audiences and merge fields.
4. Recall reconciliation runs off-peak and paced.
5. Live appointment revalidation excludes patients who already have future appointments.

### Treatment Plan And Procedure Data

1. Capability check confirms treatment/procedure support.
2. Treatment plan backfill uses `patient_id`, `status`, and `updated_since`.
3. Procedure sync uses required filters such as `updated_since`, patient, appointment, or date windows; `location_id` alone is not enough.
4. Treatment/procedure projections feed unscheduled treatment follow-up and future outcome/revenue attribution.
5. These fields are not exposed in patient SMS copy until compliance/product review approves them.

### Sync Health

1. Subscribe to sync-status webhooks for recovery signals.
2. Poll `GET /sync_status` because current webhook behavior does not reliably signal red/down transitions.
3. Feed sync freshness into launch checklist and campaign operations.
4. Pause or warn on data-driven campaign triggers when projections are stale beyond threshold.

## End-To-End Implementation Approach

1. Create a capability service based on checked-in PMS support files and NexHealth docs.
2. Generalize the NexHealth webhook ledger and processor dispatch beyond appointments.
3. Keep appointment webhook path stable, then add patient, recall-adjacent, procedure, treatment-plan, and sync-status processors incrementally.
4. Add patient working set and backfill/reconciliation.
5. Add recall working set and off-peak recall backfill.
6. Add treatment/procedure working sets for supported PMSs.
7. Add sync-status monitor and data freshness API.
8. Expose data capability/freshness to launch checklist and audience preview.
9. Wire projections into merge context, segmentation, analytics attribution, and templates.
10. Add observability and runbooks for webhook deactivation, backfill drift, and rate-limit pressure.

## Timeline

Estimated duration: 4 weeks for the campaign-critical data foundation.

- Days 1-3: capability service, generalized watermarks, and data freshness contract.
- Days 4-7: patient working set, patient backfill, patient webhook processing.
- Days 8-12: recall working set, recall backfill/reconciliation, PMS unsupported states.
- Days 13-16: sync-status monitor and launch-checklist integration.
- Days 17-20: treatment/procedure projections for supported PMSs and attribution-ready events.
- Days 21-22: audience/merge/analytics integration tests.
- Days 23-24: staging pilot with one NexHealth location and operational runbook.

## Architecture Decisions

- NexHealth remains the system of record.
- Local projections are disposable working sets, not a full PMS replica.
- Store the minimum fields needed for campaign eligibility, merge context, and attribution.
- Use webhooks for go-forward freshness; use REST for initial history, backfill, and repair.
- Revalidate live before outreach and before PMS write actions.
- Capability-check every PMS-specific data family before exposing it in templates or filters.

## Technical Considerations

- NexHealth response/version behavior is changing. Current public docs describe v3.0.0/v20240412, while the repo currently pins the stable v2 Accept header. Migration should be evaluated separately; the campaign plan must not assume a silent version switch.
- Cursor pagination is standard on current endpoints; backfills need durable watermarks and idempotency.
- `updated_since` can be inclusive, so backfill processors need dedup keys and overlap windows.
- Webhook endpoint deactivation after failures means the receiver should return 2xx for non-retryable ignored events and dead-letter processable failures.
- Shared API key rate limits require pacing across backfill, reconciliation, send-time revalidation, and booking actions.
- Treatment/procedure data can include sensitive clinical and financial details. Keep projections minimal and avoid copy exposure by default.

## Dependencies

- Launch checklist.
- Audience preview and segmentation.
- Rich merge fields.
- Patient response handling and analytics.
- Current NexHealth integration version and subscription path used by the production adapter.
- Product/compliance policy: procedure/treatment fields are stored for campaign decisions and analytics, but are not exposed in patient-facing messages by default.

## Edge Cases

- Webhook arrives before initial backfill completes.
- Webhook references unknown location.
- Patient webhook updates a record not linked to any local contact.
- Recall endpoint unsupported for the clinic's PMS.
- Treatment plan exists but procedure data cannot be fetched due to location access.
- Sync status is green but last read timestamp is stale after a weekend.
- Backfill job hits rate limits across many locations.
- A patient books through staff between preview and send.

## Risks

- Building a broad PMS mirror increases PHI exposure and operational complexity.
- Under-building projections makes segmentation and merge fields unreliable.
- Webhook downtime silently stops automated enrollment without freshness monitoring.
- API version drift breaks processors if payload shapes change.
- Rate limits are exhausted by simultaneous backfill and campaign send-time revalidation.

## Validation Strategy

- Unit tests for capability decisions by PMS/resource.
- Unit tests for watermark and inclusive `updated_since` dedup behavior.
- Integration tests for patient, recall, appointment, treatment-plan, and procedure projection RLS.
- Webhook fixture tests for each event family.
- Backfill idempotency tests.
- Reconciliation drift tests.
- Rate-limit pacing tests.
- Manual staging test:
  - create webhook endpoint/subscriptions
  - run appointment/patient/recall backfill
  - receive appointment update
  - verify launch checklist freshness
  - preview recall audience
  - enroll and send with live revalidation

## Deployment Considerations

- Keep appointment flow unchanged while adding generalized infrastructure.
- Roll out resource families one by one:
  - appointment
  - patient
  - recall
  - sync status
  - treatment/procedure
- Start with one staging NexHealth location and one production pilot.
- Add CloudWatch/observability alerts for webhook stale, endpoint disabled, backfill failed, sync stale, and rate-limit pressure.
- Add a runbook for webhook reactivation plus backfill repair after outage.

## Future Extensibility

- Tenant/DSO-owned NexHealth API keys after token cache/client pooling are safely keyed.
- Full treatment acceptance and production recovery analytics.
- Insurance benefits expiring campaigns.
- Self-serve NexHealth integration health dashboard.
- Broader financial/payment campaign data once compliance and product scope allow it.
