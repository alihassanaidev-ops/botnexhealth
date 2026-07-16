# Essential 3 - Campaign Overview And Run Progress Implementation Plan

## What Needs To Be Built

Upgrade campaign management from basic list/detail into an operational view that shows live campaign status, latest version, readiness, current runs, progress by state, channel attempts, patient timelines, failures, suppressions, and recent outcomes.

The clinic admin should be able to answer: what is running, who is waiting, who completed, what failed, who needs staff follow-up, and whether the campaign is safe to keep running.

## Existing System Context

The backend already has:

- Workflow list/detail APIs.
- Run list/detail endpoints.
- Manual enrollment and bulk enrollment.
- Cancel run and emergency halt endpoints.
- Workflow run statuses and outcome fields.
- Channel attempt records for SMS, email, and voice.
- Usage-by-campaign data.

The frontend already has:

- `Campaigns.tsx`.
- `CampaignDetail.tsx`.
- Manual enrollment dialog.
- Basic run table.
- Campaign usage cards.
- Pause, resume, archive, cancel run, and emergency halt actions.

Current gap:

- Campaign detail still has limited progress depth.
- Run list is not a true operational surface with filters, timeline, attempts, response events, and failure diagnostics.
- Analytics and progress are partially mixed instead of clearly separated.

## Existing Components To Reuse

- Existing campaign pages and route structure.
- Existing table/filter components.
- Workflow run APIs.
- Voice attempt API.
- SMS logs and email attempt/log patterns.
- Notification/SSE infrastructure for refresh events.
- Usage-by-campaign endpoint.

## New Components Required

### Backend APIs

- `GET /automation/workflows/{workflow_id}/overview`
  - campaign summary, version, readiness snapshot, run counts, recent outcomes

- `GET /automation/workflows/{workflow_id}/runs`
  - add pagination and filters:
    - status
    - outcome
    - current node
    - next due window
    - channel
    - failure reason
    - contact search

- `GET /automation/workflows/{workflow_id}/runs/{run_id}/timeline`
  - step executions
  - channel attempts
  - inbound replies
  - voice outcomes
  - email events
  - NexHealth revalidation/skips
  - staff handoffs

- `GET /automation/workflows/{workflow_id}/operations`
  - stuck waiting runs
  - failed sends
  - suppressed/skipped runs
  - replay/cancel eligibility

### Frontend Views

- Campaign overview tab:
  - status, latest version, trigger, channels, readiness, run counters, recent outcomes

- Runs tab:
  - filterable run list with progress state, current step, next action, latest outcome

- Run detail drawer/page:
  - patient-safe timeline with masked PHI by default
  - channel attempt links
  - staff follow-up status

- Operations tab:
  - failures, stuck runs, suppressions, and replay/cancel actions

## End-To-End Implementation Approach

1. Add overview API that aggregates run counts, channel counts, readiness snapshot, and latest outcomes.
2. Add server-side pagination/filtering to run list endpoint.
3. Add indexes for workflow run status, workflow ID, current node, next timer due time, and outcome.
4. Add timeline response builder that joins step executions and channel attempt records without copying message bodies into workflow rows.
5. Add frontend tabs for Overview, Runs, Operations, and Analytics.
6. Replace raw run table with filterable progress list.
7. Add run detail drawer with timeline and masked contact context.
8. Add SSE/refetch event types for campaign overview and run list refresh.
9. Add operations filters for failed/stale/suppressed runs.
10. Add role-gated replay/cancel controls with compliance recheck.

## Timeline

Estimated duration: 2.5 weeks.

- Days 1-3: overview API, filtered run list API, indexes, and backend tests.
- Days 4-6: timeline API and channel-attempt joins.
- Days 7-9: frontend overview/runs/timeline views.
- Days 10-11: operations tab and role-gated actions.
- Days 12-13: SSE/refetch events, staging QA, and performance checks.

## Architecture Decisions

- Keep campaign operations separate from builder editing. Live campaign monitoring should not require opening the canvas.
- Use refetch-over-SSE and keep SSE payloads PHI-free.
- Keep timeline response as references/summaries, not duplicated raw SMS/email/call content.
- Keep analytics rollups separate from run progress. Progress is live operational state; analytics are outcome metrics.

## Technical Considerations

- Run timelines can touch PHI. Mask by default and audit reveal actions if reveal is supported.
- Large campaigns need cursor/pagination, not full run loads.
- Timeline joins must be resilient when a channel record is missing or delayed.
- Cancel/replay must recheck current compliance, quiet hours, consent, and appointment state.
- Group/DSO users should see rollups first and PHI-light details only when permitted.

## Dependencies

- Patient response handling for reply and handoff timeline events.
- Basic analytics plan for outcome labels and metric definitions.
- Launch checklist for readiness snapshot.
- Channel attempt record consistency across SMS/email/voice.

## Edge Cases

- Run completes while user is viewing it.
- Timer is due but dispatch worker has not picked it up yet.
- Channel vendor webhook arrives after run has moved forward.
- Contact has been deleted/anonymized by retention policy.
- Campaign version changes while older runs are still in progress.
- User cancels a run that is currently executing.

## Risks

- Querying raw runtime tables for every page load can become slow.
- Too much PHI in timeline creates compliance exposure.
- Replay controls can cause duplicate outreach if idempotency is weak.
- Users misread pending/waiting states without clear next-action timestamps.

## Validation Strategy

- API tests for filters, pagination, tenant scoping, and timeline composition.
- Frontend tests for tabs, filters, timeline rendering, empty states, and role-gated actions.
- Integration tests for run cancellation and timer cancellation.
- Load test seeded campaigns with thousands of runs.
- Manual staging test: enroll contacts, watch progress through SMS wait, response, and exit.

## Deployment Considerations

- Ship overview and filtered runs first.
- Ship timeline read-only before replay/operations controls.
- Add indexes before exposing large campaign filters.
- Add feature flag for operations actions until replay semantics are fully tested.

## Future Extensibility

- Saved run filters.
- Staff assignment on handoff items.
- Export run timeline for support/debugging.
- Cross-location operational dashboard.
