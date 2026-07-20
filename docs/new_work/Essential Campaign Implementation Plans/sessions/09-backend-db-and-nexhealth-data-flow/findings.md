# Findings And Decisions

## Requirements

- Patient webhooks are part of the documented first-scope campaign data flow.
- They are required for production contact freshness, but not as direct campaign enrollment triggers.

## Research Findings

- Plan 11 first-scope subscriptions include appointment, patient, and sync-status webhooks.
- Plan 10 says patient webhook data is used to link NexHealth patients to local contacts, keep campaign contact data fresh, support merge fields, determine active/inactive status, and support future language segmentation.
- Local consent/suppression/DNC remains the source of truth; NexHealth patient fields are contact/context hints.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Store a lightweight `patient_working_set` row. | Gives campaign services freshness/state without retaining raw webhook payloads long-term. |
| Update encrypted `contacts` from patient payloads where fields are explicit. | Prevents stale phone/email/name data from being used by outbound campaigns. |
| Grant contact-location access from NexHealth `location_ids`. | Keeps location-scoped clinic users able to see patients linked by webhook/backfill. |
| Store per-location PMS read/write sync health. | Launch checklist and runtime sends need a current operational signal without storing raw webhook payloads. |
| Subscribe to sync-status events and poll `GET /sync_status`. | NexHealth sync-status webhooks are recovery-oriented and do not reliably report read/write failures. |
| Block only known read-unhealthy stale/missing appointment sends at runtime. | Fresh local projections remain usable; stale projections are unsafe when live PMS reads are known down. |
