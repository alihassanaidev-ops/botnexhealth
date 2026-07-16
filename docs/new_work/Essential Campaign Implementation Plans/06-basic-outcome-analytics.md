# Essential 6 - Basic Outcome Analytics Implementation Plan

## What Needs To Be Built

Build a basic campaign analytics layer that proves campaign impact without waiting for full ROI attribution. It should show enrollment volume, send attempts, delivery/failure rates, patient responses, confirmations, bookings, callbacks, handoffs, opt-outs, and channel cost.

Analytics must be campaign-type aware. Appointment confirmation, recall, callback, and treatment follow-up campaigns should not all use the same success labels.

## Existing System Context

The backend already has:

- Workflow run status and outcome fields.
- SMS, email, and voice attempt records.
- Per-campaign usage attribution.
- Usage-by-campaign endpoint consumed in `CampaignDetail.tsx`.
- Workflow-tagged usage events.

The frontend already has:

- Campaign detail cost/event cards.
- Dashboard chart/card components.
- Campaign run list with outcome column.

Current gap:

- There is no normalized campaign metrics rollup.
- Outcomes are not mapped consistently by template/campaign type.
- Response events and staff handoffs are not yet feeding analytics.
- There is no clear dashboard for confirmation rate, booking rate, recall conversion, callback automation, or cost per result.

## Existing Components To Reuse

- Existing usage/cost rollups.
- Workflow run outcomes.
- Channel attempt tables.
- Response-event model from patient response handling.
- Dashboard card/chart components.
- Existing scheduled job harness for rollups.

## New Components Required

### Data Model

- `campaign_metrics_daily`
  - `institution_id`, `location_id`, `workflow_id`, `workflow_version_id`, `date`
  - enrollments, active, completed, failed, cancelled, suppressed
  - sms_sent, sms_delivered, sms_failed, sms_replied
  - voice_attempted, voice_answered, voice_voicemail, voice_failed
  - email_sent, email_delivered, email_opened, email_clicked, email_bounced
  - confirmed, booked, reschedule_requested, callback_requested, staff_handoff, opt_out
  - total_cost, cost_per_booking, cost_per_confirmation where applicable

- `campaign_outcome_definitions`
  - template/category outcome labels and success/failure grouping

### Backend APIs

- `GET /automation/workflows/{workflow_id}/analytics`
  - summary, trend, channel breakdown, outcome breakdown

- `GET /automation/campaign-analytics`
  - location/institution rollups across campaigns

### Frontend

- Analytics tab in campaign detail.
- Outcome cards by campaign type.
- Channel funnel chart.
- Daily trend chart.
- Cost summary.
- Export-ready table later; not required for v1.

## End-To-End Implementation Approach

1. Define normalized outcome taxonomy by campaign category.
2. Add daily metrics table and RLS.
3. Implement rollup service from workflow runs, channel attempts, response events, handoffs, and usage events.
4. Add incremental rollup job and manual rebuild command.
5. Add campaign analytics endpoint for one workflow.
6. Add frontend analytics tab with cards and simple charts.
7. Add template metadata links to outcome definitions.
8. Add tests comparing seeded raw events to expected rollup rows.
9. Add monitoring for rollup lag/failure.

## Timeline

Estimated duration: 2.5 weeks.

- Days 1-2: outcome taxonomy and metrics schema.
- Days 3-6: rollup service, rebuild path, and backend tests.
- Days 7-8: analytics API and tenant/location scoping.
- Days 9-11: frontend analytics tab and charts.
- Days 12-13: staging data verification and rollup monitoring.

## Architecture Decisions

- Use daily rollups for dashboards instead of querying raw events on every page load.
- Treat workflow run outcome as one signal, not the only signal. Response events and booking attribution can override or enrich it.
- Keep financial ROI as future extensibility unless reliable production/revenue source is confirmed.
- Keep metric definitions explicit by campaign category.

## Technical Considerations

- Email opens are weak signals and should not be presented as primary success.
- Booking attribution needs a defined attribution window and source:
  - direct booking link click
  - NexHealth appointment created after campaign response
  - staff-marked booked
- Cost data may lag vendor events.
- Rollups must avoid double-counting retries and duplicate vendor webhooks.
- Cancelled/suppressed runs are not failures; show them separately.

## Dependencies

- Patient response handling.
- Campaign template outcome definitions.
- Campaign overview/run progress.
- Usage/cost reporting.
- NexHealth data-flow plan for booking/appointment outcome attribution.

## Edge Cases

- A patient books outside the attribution window.
- A booking is cancelled after being counted.
- A run has multiple channel attempts before one response.
- Vendor delivery event arrives after daily rollup ran.
- Campaign version changes mid-day.
- A patient opts out after booking.

## Risks

- Misleading analytics if outcome definitions are unclear.
- Double-counting outcomes across SMS, voice, and email.
- Clinics expect revenue attribution before the data is reliable.
- Slow raw queries if rollups are skipped.

## Validation Strategy

- Unit tests for outcome mapping by campaign type.
- Rollup tests from seeded workflow runs/channel events/response events.
- API tests for location and institution scoping.
- Frontend tests for empty, partial, and populated analytics states.
- Manual staging test: confirmation campaign with sent, replied, confirmed, failed, opt-out, and cost rows.

## Deployment Considerations

- Backfill metrics for recent campaigns only at first, for example 30-90 days.
- Show rollup freshness timestamp.
- Keep ROI/recovered-production labels out of v1 unless production data is wired.
- Add alerts for rollup failures and stale analytics.

## Future Extensibility

- Revenue/recovered-production analytics from procedures/charges.
- Cross-location DSO benchmarking.
- Scheduled weekly campaign reports.
- Exportable CSV/PDF.
- A/B test analytics.
