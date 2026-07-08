# Findings: Outbound 09 - Integration & Data Layer

## Key Findings
- NexHealth appointment events can enroll workflows by resolving `InstitutionLocation.nexhealth_location_id`.
- Cross-tenant lookup for webhook routing uses a system DB context.
- Appointment offset workflows can schedule future enrollment via computed ETA.
- Appointment projection, event ledger, cancellation/reschedule handling, live revalidation, subscription lifecycle,
  backfill, and reconciliation now exist and pass focused local tests.
- Live NexHealth staging verification is still required for subscription endpoint/payload compatibility, real webhook
  payload shape, and backfill/reconciliation behavior.

## Open Questions
- What are the real NexHealth webhook event payload shapes across appointment create/update?
- What are the webhook subscription limits per NexHealth account/key?
- Do real NexHealth backfill/reconciliation responses make a dedicated `recall_eligibility_working_set` necessary,
  or is the current recall pull enough for launch?
