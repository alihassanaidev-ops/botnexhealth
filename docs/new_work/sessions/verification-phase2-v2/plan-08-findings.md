# Plan 08 — Campaign Management / Progress / Analytics UI — Verification Findings

Audited: 2026-07-03. Updated: 2026-07-08 after the Plan 08 operator slice.
Evidence-based against actual codebase.

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

## 2026-07-08 update

The UI is no longer only a read-only slice:
- `Campaigns.tsx` now fetches institution-wide outbound halt status and exposes activate/release controls.
- `CampaignDetail.tsx` now shows Plan 11 usage/cost cards from `/institution/usage/summary` and `/institution/usage/by-campaign`.
- Campaign detail now exposes per-campaign emergency halt and non-terminal run cancel.
- Campaign detail now supports manual enrollment for one existing patient into an active campaign.
- Archive actions use app Dialogs instead of browser-native `confirm()`.
- The detail card is now labeled "Runs" rather than "Enrollments."
- Backend `/outbound-halt` literal routes were moved before `/{workflow_id}` to avoid route shadowing.
- `src/test/automation-api.test.ts` covers the new frontend API wrappers.

Still deferred/not-required for current scope: CSV import/mapping/preview/commit, attributed revenue, daily campaign metrics, dead-letter/replay ops page, run timeline, SSE real-time refresh, and location scoping.

## What actually exists

### Frontend (nexus-dashboard-web/)
- **`src/pages/Campaigns.tsx`** — campaign LIST page. Real interactive.
  - Lists workflows via `listCampaigns()` → `GET /automation/workflows` (`automation-api.ts:4-7`).
  - Columns: Name, Status badge, Trigger label. NO channels column, NO enrollment counts, NO outcome metrics (plan asked for these).
  - Actions: pause, resume, archive via app Dialog. Institution-wide outbound halt status + activate/release now exists. Rows update in place. NO duplicate/activate action ("New from template" link exists → templates page).
  - Edit-in-builder link, detail chevron, loading skeletons + empty state.
- **`src/pages/CampaignDetail.tsx`** — detail page. Real interactive.
  - `getCampaign(id)` + `listCampaignRuns(id)` + usage APIs. Header shows name, status badge, trigger label + pause/resume/archive/refresh/halt.
  - Usage/cost cards show campaign cost/events and channel usage over the default Plan 11 range.
  - Manual enrollment dialog searches existing patients and calls the existing enrollment backend.
  - "Runs" card lists Run ID (truncated), Status, Outcome, Started, Completed, Elapsed, and exposes cancel for non-terminal runs. NO drill-down link to a run-detail/timeline page. NO filters. NO current-step / next-due columns.
- **`src/lib/automation-api.ts`** — wrapper now includes list/get/lifecycle, manual enroll, run list/cancel, usage summary/by-campaign, outbound halt, and per-campaign emergency halt. No CSV/dead-letter wrappers.
- **`src/router.tsx`** — routes registered: `/institution-admin/campaigns`, `/campaigns/templates`, `/campaigns/:id`, `/campaigns/:id/builder`, `/campaigns/:id/versions`. All guarded `RoleGuard allowed={["INSTITUTION_ADMIN"]}` (`:272-305`). NO `/enroll`, `/runs`, `/runs/:runId`, `/analytics`, `/operations` routes.
- **`src/components/app-sidebar.tsx:108-110`** — "Campaigns" nav item (Megaphone icon) → `/institution-admin/campaigns`.
- MISSING FE: enrollment UI, CSV import/mapping/preview, run timeline page, operations/dead-letter/replay page, SSE real-time, location scoping, attributed revenue/trend analytics.

### Backend (src/app/api/routes/automation_workflows.py, 745 lines)
Endpoints present:
- `POST ""` create, `POST /validate`, `GET ""` list, `GET /merge-fields`, `GET /{id}`, `GET /{id}/versions`, `PATCH /{id}`, `POST /{id}/publish`.
- Lifecycle: `POST /{id}/pause` (:367), `/resume` (:380), `/archive` (:393). No "activate"/"duplicate" endpoint (duplicate = create-from-template lives in templates route).
- Enrollment: `POST /{id}/enroll` (:411, manual single-contact), `POST /{id}/bulk-enroll` (:558, up to 500 contacts, async via Celery `enroll_and_start_workflow_run`, idempotency key). NO CSV upload/validate/commit endpoint. NO enrollment-batch model.
- Runs: `GET /{id}/runs` (:475, pagination via `limit` 1-500, ordered by created_at desc — but NO status/step/due filters), `GET /{id}/runs/{run_id}` (:496, returns run STATUS only, NOT a timeline), `POST /{id}/runs/{run_id}/cancel` (:514). No pause-run, no retry/replay.
- Emergency halt: `GET/POST/DELETE /outbound-halt` and `POST /{workflow_id}/emergency-halt` — backend controls exist and are now consumed by the campaign UI.
- `WorkflowRunResponse` (:87-108): id, workflow_id, status, current_step_id, outcome, started_at, completed_at, created_at. No channel attempts / timeline / step history payload.
- MISSING BE: CSV endpoints, dead-letter/stale-timer ops endpoints beyond existing replay API, run-detail timeline endpoint, attributed revenue/metrics endpoint. Usage/cost endpoints now exist in Plan 11 and are consumed by the UI.

### Data model / migrations
- NO `campaign_metrics_daily`, `campaign_enrollment_batches`, `campaign_enrollment_batch_rows` tables anywhere in `src/` or `alembic/` (grep returned nothing).
- Runs stored in `AutomationWorkflowRun` model.

### SSE
- No `campaigns_updated`, `workflow_runs_updated`, or `campaign_metrics_updated` event types added. FE pages are refresh-button/refetch-on-mount only; no live SSE wiring on these pages.

### Tests
- Backend: `tests/unit/test_automation_workflow_routes.py` — 24 tests. Covers create/list/get/publish/enroll (reject non-active, reject no-version, idempotent), get_run_status, cancel_run, validate, versions, merge-fields, response `from_model` mappers. NO tests for analytics, CSV, run-list pagination scoping specifics, dead-letter, halt in this file. (`test_automation_plan09.py` covers bulk-enroll separately.) Session progress notes "29 passed" for a focused run.
- Frontend: `automation-api.test.ts` covers wrapper endpoint contracts. There are still no page-level `Campaigns.test.tsx` / `CampaignDetail.test.tsx` render tests.

## Assessment vs scope
Delivered = campaign list + detail with lifecycle controls, manual enrollment, usage/cost cards, emergency halt controls, run cancel, and a real runs table. This is the essential operator slice. Everything heavy (CSV, attributed revenue, dead-letter replay, run-detail timeline, operations page, metrics rollups beyond usage, SSE events) remains deferred/not-required — consistent with the "only build what is required" principle.

## Bugs / gaps
- Run rows have no drill-down despite plan requiring run timelines.
- `listCampaignRuns` limit default 50 with no pagination cursor / no status filter — plan explicitly wanted filterable run lists with indexes.
- Campaign list lacks channels / enrollment counts / outcome metrics columns the plan specified.
- Only `INSTITUTION_ADMIN` can reach any campaign route; plan mentioned location users and group-admin read-only views — not implemented.

## Verdict
Genuinely functional operator slice: interactive list + detail + lifecycle actions + manual enrollment + usage cards + halt controls + run cancel, INSTITUTION_ADMIN only. Complete for the essential product scope. CSV, ops/replay, timeline, SSE, location scoping, and revenue analytics are deferred/not-required unless a future launch workflow explicitly needs them. No placeholder/fake data — it renders real backend data.
