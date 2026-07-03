# Task Plan: Outbound 09 - Integration & Data Layer

## Goal
Feed external and internal trigger events into the workflow engine safely, starting with NexHealth appointment webhooks and later recall/reactivation scans.

## Current Status
Partially complete.

## Completed
- [x] NexHealth appointment webhook receiver
- [x] Signature verification helper
- [x] Location lookup from NexHealth location id
- [x] Best-effort contact resolution
- [x] Appointment trigger task dispatch
- [x] Appointment offset enrollment ETA calculation
- [x] Bulk enrollment endpoint

## Remaining
- [ ] Confirm NexHealth multi-key and webhook subscription limits
- [ ] Harden appointment webhook payload coverage against real NexHealth samples
- [ ] Complete recall scanner patient-query logic
- [ ] Complete reactivation scanner logic
- [ ] Add backfill/retry strategy for missed webhooks
- [ ] Decide legal/product rules for recall and reactivation campaigns

## Blockers / Dependencies
- Product/NexHealth confirmation of webhook caps and multi-key assumptions.
- Product/legal classification for recall/reactivation.
- Dev B compliance gate for real outbound enrollment/send decisions.

