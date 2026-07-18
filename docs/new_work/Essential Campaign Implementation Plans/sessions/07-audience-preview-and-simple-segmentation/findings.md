# Findings And Decisions

## Requirements

- Build a constrained audience preview DSL for clinic-friendly include/exclude counts before launch.
- Persist one audience definition per workflow and retain preview summaries briefly without storing row-level PHI.
- Support practical v1 filters where local projections exist: no future appointment, last visit from appointment working set, appointment type, provider, location, and channel availability.
- Treat recall due and preferred language as explicit missing-context exclusions until patient/recall working sets land.
- Enforce preview/enroll exclusions for consent, DNC, SMS suppression, active duplicate runs, already booked/future appointments, recent contact frequency caps, and missing required merge context.
- Wire preview counts into the launch checklist and expose an Audience tab on campaign detail with masked samples.

## Research Findings

- Plans 09-12 require local projections to stay disposable and PHI-minimal; NexHealth/PMS state remains authoritative and send-time revalidation still applies.
- The repo has `contacts`, `appointment_working_set`, `automation_workflow_runs`, `consent_records`, `sms_suppressions`, and `do_not_contact`; it does not yet have patient/recall/treatment working sets.
- `appointment_working_set` did not include `provider_id` or `appointment_type_id`, so Plan 07 adds nullable projection columns and populates them from webhook/backfill payloads.
- Existing manual enrollment starts a run and relies on send-step compliance gates. The new audience enroll endpoint revalidates preview-level blockers before queueing runs.
- Launch checklist previously hard-coded unknown audience/cost. It now consumes the latest unexpired audience preview for audience and send-volume estimates; projected cost remains unknown because channel pricing config is not available.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Store only preview summaries in `campaign_audience_previews`; generate row samples on demand. | Avoid retaining PHI-heavy preview result sets while preserving idempotent enroll context. |
| Use workflow location as the default audience location scope unless `location_id_in` is explicitly set. | Prevent location-scoped campaigns from previewing the entire institution by default. |
| Exclude recall/language filters as `missing_required_merge_context` until their working sets exist. | Binding docs require capability/freshness gating instead of guessing unsupported PMS data. |
| Use workflow run history as the initial conservative frequency-cap source. | Existing outbound attempt tables are split by channel; run history provides a safe v1 blocker for 1/day and 3/7-day defaults. |
