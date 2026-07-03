# Findings: Outbound 08 - Campaign Management & Progress UI

## Key Findings
- The app uses `RoleGuard` for route-level role enforcement.
- Campaigns routes are `INSTITUTION_ADMIN` only.
- Campaign list/detail can work against current automation API contract.
- Full analytics is not possible yet because there are no real channel outcome events or usage/cost records.

## Manual Verification Findings
- Institution admin can see the Campaigns nav item.
- Campaign detail renders correctly.
- Pause/resume/archive work after backend response-loading fixes.
- Archived campaigns correctly hide lifecycle actions.

## Bugs Found During Verification
- Backend response tried to lazy-load `current_version` after route session close.
- Lifecycle responses tried to read expired `updated_at`, causing `MissingGreenlet`.
- Workflow lookup helper had argument order reversed.

