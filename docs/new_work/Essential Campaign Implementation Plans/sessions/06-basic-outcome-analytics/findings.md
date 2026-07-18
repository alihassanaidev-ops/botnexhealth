# Findings And Decisions

## Requirements

- Build a normalized campaign analytics layer using daily rollups rather than raw dashboard queries.
- Keep v1 operational: enrollments, run statuses, channel attempts, delivery/failure, responses, confirmations, bookings, handoffs, opt-outs, and cost.
- Use campaign-type-aware labels for appointment confirmation, recall, callback, treatment, reactivation, and generic appointment ops.
- Do not present revenue/ROI/recovered-production labels in v1.

## Research Findings

- Existing sources available for rollup:
  - `automation_workflow_runs` for enrollment, run state, terminal outcomes, cancellation/failure/suppression signals.
  - `sms_history_logs` for outbound SMS attempts and delivery/failure status.
  - `workflow_voice_attempts` for voice attempt lifecycle and normalized dial outcomes.
  - `usage_events` for workflow-attributed email sends and channel cost.
  - `campaign_response_events` for deterministic patient response outcomes from SMS/voice/email/booking-link/staff sources.
  - `campaign_staff_handoffs` for human follow-up counts.
- Existing `CampaignDetail.tsx` had an Analytics tab, but it only rendered raw `overview.outcome_counts` and `overview.response_counts`.
- Existing Plan 05 template metadata already carries category/outcome context; instantiated workflows persist `category`, so analytics can infer labels from workflow category/trigger/name without a product decision.
- Binding Plan 09-12 context requires conservative booking attribution. Plan 06 counts only explicit booking response/run outcomes, not inferred PMS revenue or procedure value.

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| Use `campaign_metrics_daily` keyed by institution/location/workflow/version/date. | Keeps the dashboard path fast and makes version changes visible without scanning raw events. |
| Use the existing all-zero location sentinel for null locations. | Matches `usage_cost_rollups` and keeps the rollup primary key non-null. |
| Seed `campaign_outcome_definitions` and also keep code-level definitions for API labels. | The table satisfies the explicit schema requirement; code constants keep the read path deterministic for tests and empty/new DBs. |
| Count bookings only from explicit `booked`-style run/response outcomes. | Avoids implying PMS/revenue attribution before Plan 09-12 data flows support it reliably. |
| Keep the institution rollup API read-only. | Rollups are rebuilt by the scheduled/admin script, not recomputed under user RLS context during page loads. |
