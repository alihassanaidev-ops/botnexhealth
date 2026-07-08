# Task Plan: Outbound 09 - Integration & Data Layer

## Goal
Feed external and internal trigger events into the workflow engine safely, starting with NexHealth appointment webhooks and later recall/reactivation scans.

## Current Status
Code-complete for core local/data-layer scope; live NexHealth staging verification remains.

## Completed
- [x] NexHealth appointment webhook receiver
- [x] Signature verification helper
- [x] Location lookup from NexHealth location id
- [x] Best-effort contact resolution
- [x] Appointment trigger task dispatch
- [x] Appointment offset enrollment ETA calculation
- [x] Bulk enrollment endpoint
- [x] `appointment_working_set` projection
- [x] `nexhealth_webhook_events` event ledger
- [x] Cancellation/reschedule handling
- [x] Time-aware appointment idempotency key for reschedule re-enroll
- [x] Send-time `PmsLiveRevalidationService`
- [x] NexHealth webhook subscription lifecycle table/service
- [x] Initial backfill service
- [x] Reconciliation sweep
- [x] Focused local verification: 57 Plan 09 tests passing on 2026-07-08

## Remaining
- [ ] Verify subscription create/list/health flow against a live NexHealth staging tenant
- [ ] Verify real appointment webhook payloads for created/updated/cancelled/rescheduled events
- [ ] Verify backfill filters and pagination against real `list_appointments` responses
- [ ] Verify reconciliation repairs stale/missing rows against real tenant data
- [ ] Decide after staging whether `recall_eligibility_working_set` is actually required

## Blockers / Dependencies
- NexHealth staging tenant access and callback URL.
- Real staging API key/secret access; local `.env` contains placeholders only.
- Public HTTPS callback endpoint for webhook delivery.
