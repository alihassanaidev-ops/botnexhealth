# Progress: Outbound 08 - Campaign Management & Progress UI

## Slice 1 - Read-Only Campaign Pages
- **Status:** complete
- Added campaign list and detail pages.
- Added automation frontend API wrapper and TypeScript types.
- Added routes and sidebar nav item.

## Slice 2 - Lifecycle Actions
- **Status:** complete
- Added pause/resume/archive actions from list/detail.
- Archive uses confirmation dialog.
- Rows update in place.

## Slice 3 - Manual Verification
- **Status:** mostly complete
- Verified as `INSTITUTION_ADMIN`:
  - Campaign nav item visible.
  - Campaign list page renders.
  - Non-empty campaign row renders.
  - Detail page renders.
  - Empty enrollments state renders.
  - Pause/resume/archive work.

## Slice 4 - Backend Response Fixes From UI Testing
- **Status:** complete
- Fixed async SQLAlchemy response loading issues in automation workflow routes/services.
- Focused tests: `29 passed, 1 warning`.

## Slice 5 - Analytics + Operator Safety UI
- **Status:** complete
- Added institution usage/cost cards to campaign detail, backed by Plan 11 `/institution/usage/summary` and `/institution/usage/by-campaign`.
- Added institution-wide outbound halt status plus activate/release controls on the campaign list.
- Added per-campaign emergency halt action on campaign detail.
- Added run cancel action for non-terminal runs.
- Replaced browser-native archive confirmation with app Dialogs.
- Renamed the detail card from "Enrollments" to "Runs."
- Moved backend `/outbound-halt` literal routes before `/{workflow_id}` so the UI reaches the halt endpoints reliably.
- Added focused frontend API wrapper tests for usage, halt, emergency halt, and run cancel.
- Backend verification: `APP_ENV=test UV_CACHE_DIR=/tmp/uv-cache uv run pytest tests/unit/test_rbac_route_matrix.py tests/unit/test_automation_workflow_routes.py -q` -> `501 passed, 1 warning`.

## Slice 6 - Manual Enrollment UI
- **Status:** complete
- Added a compact patient search dialog on campaign detail.
- Institution admins can enroll one existing patient into an active campaign using the existing `POST /automation/workflows/{workflow_id}/enroll` backend.
- New runs are inserted into the runs table immediately after enrollment.
- CSV/list upload remains intentionally out of scope for this completion pass.

## Remaining
- Verify STAFF and LOCATION_ADMIN browser behavior.

## Deferred / Not Required For Current Scope
- CSV import/mapping/preview/commit.
- Attributed revenue and daily campaign metric rollups once business definitions are set.
- Operations page for dead-letter/replay/stale timers.
- Run-detail timeline with channel attempts and PMS actions.
- SSE real-time refresh.
- Location scoping in the campaign list.
