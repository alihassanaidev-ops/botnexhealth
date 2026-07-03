# Part 8 - Campaign Management, Progress, And Analytics UI Implementation Plan

## What Needs To Be Built

Build the dashboard surfaces for campaign management, enrollment, real-time sequence progress, drill-down timelines, analytics, reporting, and operational visibility. This is the user-facing control plane for the workflow engine after campaigns are authored or cloned from templates.

This part is distinct from the visual builder. The builder edits workflow definitions; campaign management operates active campaigns, enrolls contacts, monitors progress, and reports results.

## Existing System Context

The frontend already has:

- Dashboard pages with stats cards and charts.
- Calls, callbacks, patients, email templates, setup, and admin pages.
- Table, tabs, progress, badge, dialog, sheet, select, calendar, and chart components.
- Institution/location context and role-based navigation.
- SSE hook and notification context for live updates.

The backend already has:

- Dashboard rollup patterns for scalable aggregate reads.
- SSE event bus with PHI-free event schemas.
- Contact APIs and callback queue APIs.
- SMS delivery logs, call records, notifications, audit logs, and dead-letter records.
- Group-admin read-only oversight model for DSO-level reporting.

Current gaps:

- No campaign list or campaign detail UI.
- No enrollment UI or CSV import flow.
- No workflow run/progress APIs or UI.
- No campaign analytics rollups.
- No usage/cost reporting UI tied to campaigns.
- Existing SSE event schema does not include workflow/campaign update event types.

## Existing Components To Reuse

- Dashboard chart/card components for analytics.
- Table and filter patterns from calls/callbacks/patients pages.
- `useSSE` hook after event schemas are extended.
- Location selector and institution context.
- Role guards and sidebar navigation.
- Existing dead-letter UI/service patterns for replay controls.
- Existing notification service for staff handoffs and campaign alerts.

## New Components Required

### Frontend Pages

- `/campaigns`
  - list workflows/campaigns for selected location
  - status, channels, enrollment counts, key outcome metrics
  - activate/pause/duplicate/archive actions

- `/campaigns/:workflowId/overview`
  - campaign configuration summary, readiness, latest version, quick metrics

- `/campaigns/:workflowId/enroll`
  - manual contact add
  - multi-select contacts
  - CSV upload, mapping, validation, preview, commit

- `/campaigns/:workflowId/runs`
  - filterable sequence progress list
  - active/waiting/completed/failed/suppressed runs
  - current step, next due time, latest outcome

- `/campaigns/:workflowId/runs/:runId`
  - run timeline with steps, channel attempts, responses, PMS actions, staff handoffs

- `/campaigns/:workflowId/analytics`
  - campaign outcomes, delivery rates, booking rates, recall conversion, confirmation rates, trends

- `/campaigns/operations`
  - failed/stuck runs, dead-letter entries, replay controls, stale timers
  - **emergency compliance halt** control (Part 1 `WorkflowDefinitionService` halt / Part 12
    `EmergencyHaltService`): terminate all in-flight runs on a workflow version when a legal/consent
    defect is found — distinct from pause (Finding 9). Super-admin/operator only, audited.
  - likely super-admin/operator first, then limited tenant admin visibility

### Backend APIs

- Campaign list/detail endpoints.
- Workflow run list/detail endpoints with pagination and filters.
- Enrollment endpoints:
  - manual contact enrollment
  - bulk contact enrollment
  - CSV upload/validate/commit
- Campaign analytics endpoints using pre-aggregated rollups where scale requires it.
- Campaign operations endpoints for failed runs, retry/replay, cancel run, pause run.
- Usage/cost summary endpoints per campaign/location/institution/group.

### Data Model

- `campaign_enrollment_batches`
  - `institution_id`, `location_id`
  - workflow/version
  - source: manual, multi-select, csv, trigger
  - submitted by
  - counts: total, valid, invalid, enrolled, skipped
  - status and timestamps

- `campaign_enrollment_batch_rows`
  - validation status per imported row
  - contact match result
  - encrypted raw row only if necessary with short retention
  - error codes

- `campaign_metrics_daily`
  - `institution_id`, `location_id`, `workflow_id`, date
  - enrollments, active/completed/failed/suppressed
  - channel attempts and outcomes
  - bookings, confirmations, recalls booked, handoffs
  - attributed revenue once definition/source is confirmed

- `usage_cost_rollups`
  - **owned by Part 11 (Usage & Cost Reporting)**; this UI consumes its endpoint contract
  - location/institution/group aggregation keys
  - per-workflow spend feeds Part 12 blast-radius/budget caps

## End-To-End Implementation Approach

1. Add backend read APIs for workflows, published versions, and runs.
2. Add campaign list page and sidebar navigation.
3. Add campaign detail overview with readiness and status actions.
4. Add enrollment batch model/API for manual and CSV enrollment.
5. Build CSV validation/preview/commit UI.
6. Add run list API and progress page with filters.
7. Add run detail timeline API and UI.
8. Extend SSE event schemas with `campaigns_updated`, `workflow_runs_updated`, and `campaign_metrics_updated`.
9. Add campaign metrics rollup job or incremental aggregation path.
10. Build analytics dashboard using existing chart components.
11. Add operations page for failed/stuck runs and replay/cancel actions.
12. Add DSO/group rollup views after institution/location views are stable.

## Architecture Decisions

- Use aggregated metrics tables for analytics instead of querying raw workflow events for every dashboard load. The existing dashboard uses `call_metrics_daily` for the same reason.
- Keep enrollment imports as batches with validation preview before commit. This prevents accidental outreach to bad or non-consented CSV rows.
- Keep SSE payloads PHI-free. Events should only tell clients what to refetch.
- Separate campaign operations from builder editing. Pausing or replaying runs should not require opening the visual builder.
- Use the selected location context for normal clinic operation; institution admins can switch locations while location users remain pinned.

## Technical Considerations

- CSV rows can contain PHI and must be encrypted or discarded quickly after validation/commit.
- Enrollment previews should show masked phone/email unless the user has a reveal permission and audit trail.
- Run detail timelines should reference channel-specific records instead of copying full SMS/email/call bodies into one payload.
- Filtering run lists by status/current step/next due time needs indexes on workflow runtime tables.
- Analytics definitions must be consistent with campaign outcome mapping from Part 6.
- Real-time progress should be refetch-based over SSE, matching existing PHI-safe event-bus design.
- Group-admin views must remain read-only and avoid PHI-heavy details.

## Dependencies

- Workflow engine tables and runtime events.
- Campaign templates and outcome mapping.
- Outbound voice/SMS/email attempt records.
- Integration/data layer for campaign trigger references (Part 9).
- Usage/cost metering for spend reporting (Part 11).
- Part 12 compliance layer (emergency halt, replay compliance recheck, campaign RBAC permissions).
- Frontend route/sidebar updates.
- Role and permission decisions for replay/cancel operations.

## Edge Cases

- CSV contains duplicate rows.
- CSV contact matches multiple existing contacts.
- CSV row has no consent proof.
- User imports contacts into the wrong selected location.
- Campaign paused during CSV validation.
- Run completes while user is viewing its detail page.
- Attempt record exists but linked vendor delivery/call record is missing.
- Metrics rollup lags behind live run state.
- Group admin requests campaign metrics across many institutions.
- Replay action would violate current consent or quiet-hours rules.

## Risks

- Raw workflow event queries can become too expensive without rollups.
- CSV import can become a compliance risk if validation is weak.
- Replaying failed runs without rechecking current compliance state can contact opted-out patients.
- Analytics can mislead users if outcome definitions differ between channels.
- Too much operational power exposed to clinic admins can create accidental duplicate outreach.

## Validation Strategy

- Frontend tests for campaign list, enrollment validation states, run list filters, and timeline rendering.
- API tests for campaign run pagination and tenant/location scoping.
- CSV validation tests for required fields, duplicates, bad phone/email, consent missing, and contact matching.
- Integration tests for enrollment batch commit creating workflow runs idempotently.
- RLS tests for enrollment batches, rows, run lists, and metrics.
- SSE schema tests for new event types.
- Rollup tests comparing `campaign_metrics_daily` to seeded raw workflow events.
- Manual staging test: clone campaign, enroll CSV, watch run progress update, inspect analytics.

## Deployment Considerations

- Add campaign navigation behind a feature flag until backend APIs are ready.
- Ship list/detail read-only first, then enrollment, then operations/replay.
- Add CSV upload limits and retention cleanup before pilot use.
- Add metrics/alarms for failed enrollment batches, rollup job failures, and stale run progress.
- Restrict replay/cancel operations to super-admin/operator initially.
- Document clinic-admin guidance for CSV enrollment and campaign monitoring.

## Future Extensibility

- Saved filters and scheduled campaign reports.
- DSO-level comparative analytics across locations.
- Attribution/revenue dashboards once revenue source is confirmed.
- Exportable CSV/PDF reports.
- Budget and usage alerts.
- Inline staff task management from run timelines.
