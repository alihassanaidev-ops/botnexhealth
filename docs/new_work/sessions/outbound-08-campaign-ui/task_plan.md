# Task Plan: Outbound 08 - Campaign Management & Progress UI

## Goal
Give institution admins a campaign management UI for viewing campaigns, lifecycle controls, and enrollment progress.

## Current Status
Partially complete. Basic campaign management UI works; full analytics/progress depends on Dev B channel outcomes, usage, and compliance data.

## Completed
- [x] Campaigns nav item for `INSTITUTION_ADMIN`
- [x] `/institution-admin/campaigns` list page
- [x] `/institution-admin/campaigns/:id` detail page
- [x] Status badges and trigger labels
- [x] Pause/resume/archive UI actions
- [x] Archive confirm dialog
- [x] Empty enrollments state
- [x] Enrollment table layout
- [x] RoleGuard route protection for institution admin pages
- [x] Manual UI verification for create/list/detail/pause/resume/archive

## Remaining
- [ ] Verify STAFF and LOCATION_ADMIN direct navigation in browser
- [ ] Add full analytics/progress once channel outcomes exist
- [ ] Add emergency halt control when Plan 12 semantics are finalized
- [ ] Add usage/cost views after Plan 11 exists
- [ ] Add richer enrollment outcome display after real delivery events exist

## Dependencies
- Plan 01 runs and timers.
- Plan 06 templates.
- Dev B Plan 11 usage/cost.
- Dev B Plan 12 compliance/emergency halt.
- Channel webhooks/outcomes from Plans 03/04/05.

