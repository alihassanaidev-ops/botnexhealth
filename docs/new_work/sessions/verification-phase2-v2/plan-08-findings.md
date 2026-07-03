# Plan 08 — Campaign Management / Progress / Analytics UI — Verification Findings

Audited: 2026-07-03. Evidence-based against actual codebase (branch `ali/phase-2`).

## Plan intent (deliverables)
Per `docs/new_work/Implementation Plans/08-campaign-management-progress-analytics-ui.md`:
- `/campaigns` list — status, channels, enrollment counts, key outcome metrics; activate/pause/duplicate/archive.
- `/campaigns/:id/overview` — config summary, readiness, latest version, quick metrics.
- `/campaigns/:id/enroll` — manual add, multi-select, CSV upload/mapping/validation/preview/commit.
- `/campaigns/:id/runs` — filterable progress list (active/waiting/completed/failed/suppressed), current step, next due, latest outcome.
- `/campaigns/:id/runs/:runId` — run timeline (steps, channel attempts, responses, PMS actions, handoffs).
- `/campaigns/:id/analytics` — outcomes, delivery/booking/recall/confirmation rates, trends, attributed revenue.
- `/campaigns/operations` — failed/stuck runs, dead-letter entries, replay controls, stale timers, emergency compliance halt.
- Backend: campaign list/detail, run list/detail w/ pagination+filters, enrollment (manual/bulk/CSV validate/commit), analytics rollups, ops (retry/replay/cancel/pause run), usage/cost.
- Data model: `campaign_enrollment_batches`, `campaign_enrollment_batch_rows`, `campaign_metrics_daily`, `usage_cost_rollups` (Plan 11).
- SSE: `campaigns_updated`, `workflow_runs_updated`, `campaign_metrics_updated`.

Note: Scope sequence doc says Phase 3 ships read-only progress; full analytics/CSV/dead-letter replay/emergency-halt come in Phase 6. So most heavy items are explicitly out of this phase's scope.

## What actually exists

### Frontend (nexus-dashboard-web/)
- **`src/pages/Campaigns.tsx`** (277 lines) — campaign LIST page. Real interactive.
  - Lists workflows via `listCampaigns()` → `GET /automation/workflows` (`automation-api.ts:4-7`).
  - Columns: Name, Status badge, Trigger label. NO channels column, NO enrollment counts, NO outcome metrics (plan asked for these).
  - Actions: pause (`:79`), resume (`:92`), archive (`:105`, uses native `confirm()` not a Dialog despite session note claiming a confirm dialog). Rows update in place. NO duplicate/activate action ("New from template" link exists at `:137` → templates page).
  - Edit-in-builder link (`:239`), detail chevron (`:262`). Loading skeletons + empty state.
- **`src/pages/CampaignDetail.tsx`** (304 lines) — detail page. Real interactive.
  - `getCampaign(id)` + `listCampaignRuns(id)` (`:81`). Header shows name, status badge, trigger label + pause/resume/archive/refresh.
  - "Enrollments" card = a READ-ONLY run table (`:235-301`): Run ID (truncated), Status, Outcome, Started, Completed, Elapsed. NO drill-down link to a run-detail/timeline page. NO filters. NO current-step / next-due columns. This is the closest thing to the "runs progress" surface but rolled into detail; no dedicated `/runs` route or `/runs/:runId` timeline exists.
  - No "overview" summary metrics beyond status/trigger.
- **`src/lib/automation-api.ts`** (37 lines) — wrapper: listCampaigns, getCampaign, pause/resume/archive, listCampaignRuns (limit param only, no status filter). No enroll/CSV/analytics/ops calls.
- **`src/router.tsx`** — routes registered: `/institution-admin/campaigns`, `/campaigns/templates`, `/campaigns/:id`, `/campaigns/:id/builder`, `/campaigns/:id/versions`. All guarded `RoleGuard allowed={["INSTITUTION_ADMIN"]}` (`:272-305`). NO `/enroll`, `/runs`, `/runs/:runId`, `/analytics`, `/operations` routes.
- **`src/components/app-sidebar.tsx:108-110`** — "Campaigns" nav item (Megaphone icon) → `/institution-admin/campaigns`.
- MISSING FE: enrollment UI, CSV import/mapping/preview, run timeline page, analytics/charts page, operations/dead-letter/replay page, emergency-halt control UI. No `analytics`, `csv`, `replay`, `dead-letter` references anywhere in `nexus-dashboard-web/src/`.

### Backend (src/app/api/routes/automation_workflows.py, 745 lines)
Endpoints present:
- `POST ""` create, `POST /validate`, `GET ""` list, `GET /merge-fields`, `GET /{id}`, `GET /{id}/versions`, `PATCH /{id}`, `POST /{id}/publish`.
- Lifecycle: `POST /{id}/pause` (:367), `/resume` (:380), `/archive` (:393). No "activate"/"duplicate" endpoint (duplicate = create-from-template lives in templates route).
- Enrollment: `POST /{id}/enroll` (:411, manual single-contact), `POST /{id}/bulk-enroll` (:558, up to 500 contacts, async via Celery `enroll_and_start_workflow_run`, idempotency key). NO CSV upload/validate/commit endpoint. NO enrollment-batch model.
- Runs: `GET /{id}/runs` (:475, pagination via `limit` 1-500, ordered by created_at desc — but NO status/step/due filters), `GET /{id}/runs/{run_id}` (:496, returns run STATUS only, NOT a timeline), `POST /{id}/runs/{run_id}/cancel` (:514). No pause-run, no retry/replay.
- Emergency halt: `GET/POST/DELETE /outbound-halt` (:632/:663/:718) — outbound halt controls EXIST at backend (Plan 12 area) but no FE surface consumes them here.
- `WorkflowRunResponse` (:87-108): id, workflow_id, status, current_step_id, outcome, started_at, completed_at, created_at. No channel attempts / timeline / step history payload.
- MISSING BE: analytics/metrics endpoints, CSV endpoints, dead-letter/replay endpoints, usage/cost endpoints, run-detail timeline endpoint.

### Data model / migrations
- NO `campaign_metrics_daily`, `campaign_enrollment_batches`, `campaign_enrollment_batch_rows` tables anywhere in `src/` or `alembic/` (grep returned nothing).
- Runs stored in `AutomationWorkflowRun` model.

### SSE
- No `campaigns_updated`, `workflow_runs_updated`, or `campaign_metrics_updated` event types added. FE pages are refresh-button/refetch-on-mount only; no live SSE wiring on these pages.

### Tests
- Backend: `tests/unit/test_automation_workflow_routes.py` — 24 tests. Covers create/list/get/publish/enroll (reject non-active, reject no-version, idempotent), get_run_status, cancel_run, validate, versions, merge-fields, response `from_model` mappers. NO tests for analytics, CSV, run-list pagination scoping specifics, dead-letter, halt in this file. (`test_automation_plan09.py` covers bulk-enroll separately.) Session progress notes "29 passed" for a focused run.
- Frontend: NO campaign tests. No `Campaigns.test.tsx` / `CampaignDetail.test.tsx` in `nexus-dashboard-web/src/test/`. Plan's validation strategy called for FE tests on list, enrollment states, run filters, timeline — none exist.

## Assessment vs scope
Delivered = read-only-ish campaign list + detail with lifecycle controls (pause/resume/archive) and a read-only run table. This matches "Phase 3 read-only progress" from the sequence doc. Everything heavy (CSV, analytics, attributed revenue, dead-letter replay, emergency-halt UI, run-detail timeline, operations page, enrollment UI, metrics rollups, SSE events) is Phase-6-deferred and NOT built here — consistent with the session task_plan's "Remaining" list and stated dependencies on Plans 11/12 and channel outcomes.

## Bugs / gaps
- Archive uses browser-native `confirm()` (`Campaigns.tsx:106`, `CampaignDetail.tsx:122`), not the app's Dialog component — session findings/progress claim an "Archive confirm dialog"; that is inaccurate for these pages (a Dialog exists only in `WorkflowPublishControls.tsx:105`, a different builder surface).
- Detail "Enrollments" card conflates workflow runs with enrollments; run rows have no drill-down despite plan requiring run timelines.
- `listCampaignRuns` limit default 50 with no pagination cursor / no status filter — plan explicitly wanted filterable run lists with indexes.
- Campaign list lacks channels / enrollment counts / outcome metrics columns the plan specified.
- Only `INSTITUTION_ADMIN` can reach any campaign route; plan mentioned location users and group-admin read-only views — not implemented.

## Verdict
Genuinely functional but minimal slice: interactive list + detail + lifecycle actions + read-only run view, INSTITUTION_ADMIN only, no tests on FE. ~20-25% of the full plan; ~90-100% of the intentionally-scoped Phase-3 read-only subset. No placeholder/fake data — it renders real backend data.
