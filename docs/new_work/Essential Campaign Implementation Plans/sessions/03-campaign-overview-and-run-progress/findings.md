# Findings And Decisions

## Requirements

- Add operational campaign overview with version, readiness, run counts, channels, and recent outcomes.
- Add filtered, paginated campaign run list.
- Add PHI-light run timeline composed from existing runtime, timer, event, SMS, inbound SMS, voice, and usage rows.
- Add operations view for stuck waiting runs, failed sends, suppressions/skips, and cancel/replay eligibility.
- Keep analytics rollups separate from live progress.

## Research Findings

- Graphify identified `CampaignDetail.tsx`, `automation-api.ts`, shared frontend workflow/run types, and `automation_workflows.py` as the existing implementation surface.
- Existing backend data is sufficient for read-only Plan 03 views: workflow runs, versions, step executions, timers, workflow events, SMS history logs, inbound SMS messages, workflow voice attempts, and usage events.
- There is no durable email attempt table yet; email timeline entries use usage/events where available instead of inventing an email log in this plan.
- Existing cancel-run and emergency-halt actions are the safe write actions for operations V1.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Use a dedicated `CampaignOperationsService` | Keeps overview/list/timeline/operations SQL out of the route module and makes the operational projection reusable. |
| Return cursor-paginated run lists | Aligns with Plan 03 scale guidance and Plan 11's cursor-pagination direction for campaign-used list endpoints. |
| Do not expose raw message bodies or decrypted contact fields in timeline | Preserves PHI-light operational monitoring and follows the decision docs. |
| Add indexes only, not new Plan 03 data tables | Existing runtime tables can support the operational view; new response/handoff tables are owned by later plans. |
