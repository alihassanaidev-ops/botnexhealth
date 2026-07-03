# Findings: Outbound 09 - Integration & Data Layer

## Key Findings
- NexHealth appointment events can enroll workflows by resolving `InstitutionLocation.nexhealth_location_id`.
- Cross-tenant lookup for webhook routing uses a system DB context.
- Appointment offset workflows can schedule future enrollment via computed ETA.
- Recall scanning currently exists as a stub and needs real patient/recall query rules.

## Open Questions
- What are the real NexHealth webhook event payload shapes across appointment create/update?
- What are the webhook subscription limits per NexHealth account/key?
- Which recall/reactivation campaigns are transactional vs marketing?
- What missed-webhook backfill window is required?

