# Findings: Outbound 08 - Campaign Management & Progress UI

## Key Findings
- The app uses `RoleGuard` for route-level role enforcement.
- Campaigns routes are `INSTITUTION_ADMIN` only.
- Campaign list/detail can work against current automation API contract.
- Usage/cost analytics are now possible because Plan 11 shipped usage rollups and by-campaign APIs.
- Full outcome analytics and attributed revenue still need product/business definitions.

## Manual Verification Findings
- Institution admin can see the Campaigns nav item.
- Campaign detail renders correctly.
- Pause/resume/archive work after backend response-loading fixes.
- Archived campaigns correctly hide lifecycle actions.
- Campaign detail now shows usage/cost cards from real usage APIs.
- Campaign detail now supports manual enrollment for one existing patient.
- Campaign list now exposes institution-wide outbound halt status and activate/release actions.
- Campaign detail now exposes per-campaign emergency halt and run cancel actions.

## Bugs Found During Verification
- Backend response tried to lazy-load `current_version` after route session close.
- Lifecycle responses tried to read expired `updated_at`, causing `MissingGreenlet`.
- Workflow lookup helper had argument order reversed.
- `GET /automation/workflows/outbound-halt` was declared after `GET /{workflow_id}` and could be captured as a workflow id. The literal halt routes now sit before the parameterized route.

## Remaining Product Gaps
- CSV import/mapping/preview/commit is still not built because it is not required for the current campaign scope and introduces PHI/consent/retention decisions.
- Attributed revenue and `campaign_metrics_daily` remain undefined/deferred.
- Operations page for dead-letter/replay/stale timers remains deferred.
- Run-detail timeline and SSE real-time updates remain deferred.

## Completion Decision
Plan 08 is complete for the essential product scope: admins can list/manage campaigns, view runs, see usage/cost, manually enroll an existing patient, cancel runs, and halt outbound safely. The unbuilt items are deferred/not-required extensions rather than blockers.
