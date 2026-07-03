# Part 11 - Usage And Cost Reporting Implementation Plan

> **Why this plan exists.** Scope §14 lists **"Usage & cost reporting"** as a distinct
> deliverable, §9.4 requires it in the analytics UI, and §12 requires usage metering as an
> operational capability. Part 8 already depends on it (it defines `usage_cost_rollups`
> "shared with Part 11 later" and lists "Usage/cost summary endpoints per
> campaign/location/institution/group"), and Parts 3/4/5 each capture raw per-interaction usage
> fields (Retell dials/minutes, Twilio segments/minutes, provider price) with nothing to
> aggregate them. This plan is the missing aggregation/reporting layer that closes those
> references. It is a genuine coverage gap, not a re-plan of an existing part.

## What Needs To Be Built

Build the metering, aggregation, and reporting layer that captures per-interaction consumption
from Retell (connected minutes, dials) and Twilio (SMS segments, voice minutes), plus email
send counts from Resend, tags each record by location/institution/(DSO) group, and rolls it up
the location → institution → DSO hierarchy for reporting and optional budget controls.

This is a **reporting/metering** layer. The hard enforcement controls (per-workflow spend caps,
blast-radius limits) are owned by Part 12; this plan supplies the **usage data** those controls
and dashboards read.

## Existing System Context

The backend already has:

- Retell post-call pipeline (`PostCallService`) and `Call` records with `retell_call_id`.
- Twilio `sms_history_logs`, delivery-status webhook, and (Part 4) `provider_segments` /
  `price_amount` fields on SMS attempts.
- (Part 3) `workflow_voice_attempts`; (Part 5) `workflow_email_attempts` with cost/usage fields.
- `call_metrics_daily` and dashboard rollup patterns for scalable aggregate reads.
- Group-admin read-only oversight model for DSO-level reporting.
- Per-tenant Twilio sub-accounts (Part 10) giving natural per-clinic cost attribution.

Current gaps:

- No unified usage-event model spanning voice/SMS/email/inbound + outbound.
- No location → institution → DSO cost rollup tables or job.
- No usage/cost API or UI surface tied to campaigns and channels.
- No ingestion of Retell/Twilio billing signals (per-dial $, per-segment $, per-minute $).

## Existing Components To Reuse

- `call_metrics_daily` rollup pattern and the daily-aggregation job harness.
- Retell post-call and Twilio status webhooks as the usage-signal source (extended, not duplicated).
- SSE event bus for `usage_metrics_updated` hints.
- Group-admin oversight scoping for DSO rollups.
- Audit/PHI-safe logging conventions (usage rows are cost metadata, PHI-free by construction).

## New Components Required

### Data Model

- `usage_events`
  - `institution_id`, `location_id`, optional `institution_group_id` (denormalized for rollup)
  - channel: `voice`, `sms`, `email`
  - direction: `inbound`, `outbound`
  - source system: `retell`, `twilio`, `resend`
  - source reference (retell call id / twilio SID / provider message id) — NOT PHI
  - optional `workflow_run_id`, `workflow_id`, `campaign_key`, `workflow_step_id`
  - quantity metrics: `minutes`, `dials`, `sms_segments`, `emails`
  - `cost_amount`, `cost_currency` when the provider exposes billing; else null (estimated flag)
  - `occurred_at`, `ingested_at`, idempotency key (source system + source reference + metric type)

- `usage_cost_rollups` (the contract Part 8 depends on)
  - aggregation keys: `location_id` | `institution_id` | `institution_group_id`, `date`, `channel`, `direction`
  - summed minutes/dials/segments/emails, summed cost, currency
  - optional `workflow_id`/`campaign_key` dimension for per-campaign spend
  - unique on (scope key, date, channel, direction, campaign dimension)

- Optional `usage_budgets` (read side of Part 12's caps)
  - scope (location/institution/group), period, budget amount/currency, soft/hard thresholds
  - **enforcement** lives in Part 12; this table is the shared source of truth both read.

Every tenant-scoped table gets `institution_id`, RLS, and indexes by scope key + date.

### Services

- `UsageMeteringService`
  - idempotently records `usage_events` from Retell post-call, Twilio status callbacks, and
    Resend webhooks (hooks into the same webhook handlers Parts 3/4/5 already touch).
  - tags each event with tenant + workflow/campaign context when the interaction originated
    from a workflow attempt; inbound/manual usage is tagged with location only.

- `UsageRollupService`
  - daily (and incremental) aggregation into `usage_cost_rollups`, mirroring `call_metrics_daily`.
  - hierarchical rollup location → institution → DSO group.

- `UsageReportingService`
  - serves the API contract Part 8 consumes (summary + trend + per-campaign spend).
  - exposes current-period consumption to Part 12's budget/threshold evaluation.

### Backend APIs (consumed by Part 8 UI)

- Usage/cost summary per location / institution / group, per channel, per period.
- Per-campaign spend and per-channel breakdown.
- Trend series for charts.
- Budget status (consumed vs threshold) when `usage_budgets` is configured.

## End-To-End Implementation Approach

1. Add `usage_events` with RLS and idempotent ingestion keys.
2. Hook Retell post-call, Twilio status, and Resend webhook handlers (Parts 3/4/5) to emit usage
   events — one write path, defense-in-depth idempotent.
3. Backfill/estimate cost using provider pricing where the webhook lacks a price
   (Retell $0.005/dial + per-minute; Twilio per-segment/minute) with an `estimated` flag.
4. Add `usage_cost_rollups` and the daily/incremental rollup job (EventBridge → Fargate pattern).
5. Add `UsageReportingService` + APIs; wire Part 8 UI to the real endpoints.
6. Expose current-period consumption to Part 12 for budget/threshold checks.
7. Add DSO-group hierarchical rollups after location/institution rollups are stable.
8. Metrics/alarms for ingestion lag, rollup failures, and untagged usage.

## Architecture Decisions

- **Meter from vendor webhooks, not app-side estimates**, so usage reflects actual billed
  consumption; estimate only when the provider omits price, flagged as such.
- **Single unified `usage_events` model** across channel/direction so inbound and outbound spend
  aggregate together (§12 requires inbound + outbound).
- **Usage data (Part 11) is separate from enforcement (Part 12).** Reporting should never block a
  send; caps do — and they live in the compliance/controls layer that reads this data.
- **Rollups, not raw scans**, for dashboards — same rationale as `call_metrics_daily`.
- **Sub-account attribution**: Twilio per-clinic sub-accounts (Part 10) make per-clinic Twilio
  cost attribution native; Retell per-workspace usage maps to tenant via profile (Part 3).

## Technical Considerations

- Usage rows are cost metadata and must stay PHI-free (source references, not transcripts/bodies).
- Provider price fields can arrive **after** the interaction (async billing) — treat cost as an
  update to an existing event, keyed idempotently.
- Retell per-workspace/per-DSO models must map workspace → tenant to attribute minutes correctly.
- Estimated vs. actual cost must be distinguishable in the UI to avoid over-trusting estimates.
- DSO rollups must respect group-admin read-only scoping and avoid PHI.

## Dependencies

- Parts 3/4/5 attempt records and their provider webhook handlers (usage signal source).
- Part 10 provisioning (workspace/sub-account → tenant mapping).
- Part 8 UI (consumer of the reporting APIs).
- Part 12 (consumer of current-period usage for budget/spend enforcement).

## Edge Cases

- Provider webhook with price arrives after the run/campaign has completed.
- Retell shared-workspace (per-DSO) usage that must be split across child locations.
- Inbound usage (existing inbound agent) that has no workflow/campaign context.
- Duplicate provider webhooks (idempotent ingestion).
- A clinic on platform-fallback credentials (Part 10 migration) — attribute to platform, flagged.
- Currency mismatch across regions (US/CA) in a single DSO rollup.

## Risks

- Under-tagged usage events make per-campaign spend unreliable.
- Estimated costs drifting from real invoices erodes trust in the dashboard.
- Rollup job failures silently stall spend visibility — needs alarms.
- Coupling enforcement into reporting would let a metering bug block legitimate sends (avoided by
  the Part 11 / Part 12 split).

## Validation Strategy

- Unit tests for idempotent usage ingestion (dup webhooks, late price updates).
- Unit tests for cost estimation vs. provider-supplied price.
- Unit tests for hierarchical rollup math (location → institution → group).
- RLS tests for usage tables and rollups.
- Integration test: outbound SMS/voice/email attempt → usage event → daily rollup → API total.
- Reconciliation test comparing rollups to seeded raw usage events.

## Deployment Considerations

- Ship `usage_events` + ingestion before rollups/UI to accumulate history early.
- Feature-flag the usage UI in Part 8 until rollups are validated.
- Add alarms for ingestion lag, rollup failures, untagged/estimated-cost ratio.
- Backfill a window of historical Retell/Twilio usage where provider APIs allow, for launch.

## Future Extensibility

- Per-tenant invoicing/export.
- Budget caps and automated throttling (enforcement handshake with Part 12).
- Cost-per-outcome analytics (spend per confirmed appointment / recall booked) joined to Part 8.
- Provider-invoice reconciliation.
