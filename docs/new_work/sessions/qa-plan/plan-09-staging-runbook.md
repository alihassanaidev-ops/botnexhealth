# Plan 09 — NexHealth Staging Validation Runbook

**Why:** Plan 09's code is complete and unit-tested against a *mocked* NexHealth client
(~80%). The remaining 20% is proving the four resilience flows against a **live/staging
NexHealth tenant** — the only thing blocking Plan 09 to 100%. This is a QA/ops exercise, not
implementation. With the prerequisites in place it's roughly a 1-hour run.

## Prerequisites (ops must provision)
- ☐ staging `NEXHEALTH_API_KEY`
- ☐ staging `NEXHEALTH_WEBHOOK_SECRET`
- ☐ a real `InstitutionLocation` with `nexhealth_subdomain` + `nexhealth_location_id`
- ☐ public HTTPS callback URL: `https://<staging-api>/api/v1/nexhealth/webhooks/appointments`
- ☐ `NEXHEALTH_WEBHOOK_CALLBACK_URL` set (gates provider subscription creation)
- ☐ staging worker + beat running (or the ability to run the Celery tasks manually)

## Flow 1 — Subscription lifecycle
1. Trigger `ensure_for_configured_locations` (beat runs hourly, or invoke manually).
2. `NexHealthSubscriptionLifecycleService.create/list/health`.
- **Expect:** a subscription is created at NexHealth for the location's event types; list returns it; health-check reports active. Confirm the **exact partner subscription endpoint + payload shape** matches what the code sends (the mock-only unknown).

## Flow 2 — Real webhook payloads
1. Create/update/cancel/reschedule an appointment in the staging PMS (or have NexHealth replay one).
2. Webhook hits `/api/v1/nexhealth/webhooks/appointments`.
- **Expect:** signature verifies (fail-closed if secret missing in prod); `nexhealth_webhook_events` ledger claims the event (dedup); `appointment_working_set` UPSERTs; a `new` appointment triggers workflow enrollment. Replay the same event → idempotent (ledger blocks the duplicate). Confirm the **real payload field names** match the parser.

## Flow 3 — Initial REST backfill
1. Run the backfill (`NexHealthAppointmentSyncService` / `nexhealth_backfill_service`) for the location.
- **Expect:** `NexHealthAdapter.list_appointments` **pagination + date filtering** work against the real API; go-forward appointments UPSERT the projection and trigger workflows. Confirm paging (cursor/offset) behaves as coded — this is the second mock-only unknown.

## Flow 4 — Reconciliation sweep + reschedule re-enroll (closes Finding E)
1. Let the paced reconciliation beat run (every 6h, or invoke manually) after mutating appointments in the PMS.
- **Expect:** stale/missing projection rows repaired; runs for cancelled appointments terminated (+ timers cancelled); a **rescheduled** appointment cancels the old runs and **re-enrolls at the new time** via the time-aware idempotency key (`appt:{version}:{id}:{start}`) — reminder moved, not dropped or duplicated.

## After the run
- ☐ Decide **D-6**: is a dedicated `recall_eligibility_working_set` projection actually needed, now that real recall/backfill behavior is observed? (Deferred pending this data.)
- ☐ Update `../outbound-followups-and-gaps.md` (D-5/D-6) + the v2/v3 reports with the staging result.
- ☐ If all four pass → Plan 09 → 100%.

## Notes
- The projection/ledger/reschedule/freshness **code** is already Postgres-verified (57 local unit tests). This runbook only exercises the **live-NexHealth** seams that mocks can't prove.
- Any payload/endpoint mismatch found here is a small parser/adapter fix, not a redesign.
