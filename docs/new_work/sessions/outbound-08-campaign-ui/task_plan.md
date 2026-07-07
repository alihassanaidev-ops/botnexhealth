# Task Plan: Outbound 08 - Campaign Management & Progress UI

## Goal
Give institution admins a campaign management UI for viewing campaigns, lifecycle controls, and enrollment progress.

## Current Status
Essential operator scope complete. Campaign management UI works, usage/cost analytics consume Plan 11 APIs, emergency halt controls consume the Plan 12 halt backend, and manual enrollment is exposed for existing patients. CSV import, attributed revenue, run timelines, ops replay, and SSE are not required for the current agreed scope.

## Completed
- [x] Campaigns nav item for `INSTITUTION_ADMIN`
- [x] `/institution-admin/campaigns` list page
- [x] `/institution-admin/campaigns/:id` detail page
- [x] Status badges and trigger labels
- [x] Pause/resume/archive UI actions
- [x] Archive confirm dialog
- [x] Empty runs state
- [x] Runs table layout
- [x] Campaign detail usage/cost cards backed by `/institution/usage/summary` and `/by-campaign`
- [x] Manual enrollment UI for one existing patient using the existing enrollment endpoint
- [x] Institution-wide outbound halt status + activate/release UI
- [x] Per-campaign emergency halt UI
- [x] Run cancel action UI
- [x] Backend literal halt routes ordered before `/{workflow_id}`
- [x] Frontend API wrapper coverage for usage, halt, emergency halt, and run cancel
- [x] RoleGuard route protection for institution admin pages
- [x] Manual UI verification for create/list/detail/pause/resume/archive

## Remaining
- [ ] Verify STAFF and LOCATION_ADMIN direct navigation in browser
- [ ] Verify STAFF and LOCATION_ADMIN direct navigation in browser

## Deferred / Not Required For Current Scope
- CSV import/mapping/validation/preview/commit — not required for the current four-campaign scope; adds PHI/consent/retention complexity and should only be built if list-upload enrollment becomes a launch workflow.
- Attributed revenue + `campaign_metrics_daily` — requires confirmed revenue source and attribution definition.
- Operations page for dead-letter/replay/stale timers — high-volume support tooling, not needed for the operator pilot.
- Run-detail timeline with channel attempts / PMS actions / handoffs — support/debugging enhancement.
- SSE real-time refresh for run and metric updates — manual refresh is acceptable for current use.
- Location scoping in the campaign list — only required when multi-location campaign operation is in scope.
- Richer outcome display after delivery/booking outcomes are product-defined.

## Dependencies
- Plan 01 runs and timers.
- Plan 06 templates.
- Dev B Plan 11 usage/cost.
- Dev B Plan 12 compliance/emergency halt.
- Channel webhooks/outcomes from Plans 03/04/05.
