# NexHealth v3 Cutover Runbook

This runbook captures the stable operating procedure for moving NexHealth traffic from the legacy v2 contract to the stable v3 contract. Task ownership and progress live in ClickUp; this document records the repeatable migration procedure.

## Scope

- REST cutover: outgoing NexHealth API requests move to `Nex-Api-Version: v3.0.0`.
- Webhook cutover: NexHealth webhook subscriptions are recreated so delivered payloads use the v3 contract.
- REST and webhook cutovers are separate. Existing v2 webhook subscriptions remain v2-shaped until replaced.

## Preflight

- Confirm the configured NexHealth API contract target normalizes to `stable_v3`.
- Confirm startup rejects unknown NexHealth version values.
- Confirm request headers are derived from the normalized contract target.
- Confirm `/available_slots` and `/working_hours` routing is enabled for v3.
- Confirm `/available_slots` shares the conservative appointment/slot read rate-limit class.
- Confirm booking revalidates the exact selected slot through the contract-aware
  slot endpoint before `POST /appointments`.
- Run appointment and patient backfills before cutover to establish baseline counts.

## Staging REST Validation

- Patient lookup.
- Slot search.
- Appointment booking, including stale selected-slot failure that returns
  fresh-slot guidance.
- Appointment cancellation.
- Appointment confirmation.
- Reschedule flow.
- Appointment backfill.
- Patient backfill.
- Sync-status polling.

## Production REST Cutover

- Cut over during a low-traffic window.
- Deploy/restart with the v3 REST configuration.
- Run production smoke tests for patient lookup, slot search, booking, cancel, confirm, and reschedule.
- Run appointment and patient backfills after cutover and compare against baseline counts.

## REST Rollback

Rollback is an intentional deploy/restart config change, not a dynamic database flag.

Rollback triggers include:

- Confirmed booking failures above baseline.
- Patient lookup failures above baseline.
- Slot-search failures.
- Appointment projection gaps after post-cutover backfill.

Optional enrichment differences are not rollback triggers by themselves.

## Webhook Shadow Validation

- Configure `NEXHEALTH_SHADOW_WEBHOOK_CALLBACK_BASE_URL` to the public API origin.
- Manually run `src.app.tasks.automation_workflow.ensure_nexhealth_shadow_webhook_subscriptions` when staging is ready. This task is not in Celery beat; shadow subscriptions must be created intentionally.
- Shadow subscriptions use stable-v3 request headers and point event groups at distinct routes:
  - `POST /api/v1/nexhealth/webhooks/shadow/appointments`
  - `POST /api/v1/nexhealth/webhooks/shadow/patients`
  - `POST /api/v1/nexhealth/webhooks/shadow/sync-status`
- Shadow deliveries are stored in `nexhealth_webhook_shadow_events`, not the live `nexhealth_webhook_events` ledger. Lifecycle state is tracked separately in `nexhealth_webhook_shadow_subscriptions`.
- Verify each event type extracts business event identity, institution/location resolution, patient id, appointment id, start time, provider id, appointment type id, cancellation state, event time, and dedup basis.
- Shadow parse failures are captured with encrypted raw payloads, redacted payloads, payload hashes, and `parse_status=failed`, then acknowledged with 2xx so NexHealth does not disable validation endpoints.

## Webhook Cutover

- After shadow validation passes, point v3 subscriptions at the real handlers.
- Keep the v2-pinned subscriptions active during the initial live-v3 window so
  overlap is observable and rollback is still practical.
- Confirm live handlers accept v3-shaped appointment, patient, and sync-status
  payloads on:
  - `POST /api/v1/nexhealth/webhooks/appointments`
  - `POST /api/v1/nexhealth/webhooks/patients`
  - `POST /api/v1/nexhealth/webhooks/sync-status`
- Deduplicate real overlap by business event identity, not provider delivery id
  or provider subscription id. The live ledger key is
  `Resource:pms_resource_id:event_family:change_marker`.
- Verify duplicate v2/v3 deliveries for the same PMS change return
  `status=duplicate` and do not enqueue a second workflow or rewrite the
  projection.
- For v3 patient payloads without `location_ids`, verify single-location
  subdomains still update the patient/contact projection. Multi-location
  subdomains without explicit location ids must not grant location visibility
  blindly; use backfill/reconciliation to restore scoped visibility if needed.
- Delete v2-pinned subscriptions only after v3 live handling passes monitoring.

## Cleanup Criteria

After one stable production release cycle on v3, remove temporary migration scaffolding:

- v2 path routing.
- v2 response-shape branches no longer needed for live traffic.
- legacy `Accept` header compatibility.
- v2 webhook overlap code.
- temporary migration metrics once exported dashboards no longer use them.
