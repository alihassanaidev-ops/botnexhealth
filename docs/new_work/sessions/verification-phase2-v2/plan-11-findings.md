# Plan 11 — Usage & Cost Reporting — Verification Findings

Original audit date: 2026-07-03. Updated: 2026-07-07 after Plan 11 closeout changes.
Plan file: `docs/new_work/Implementation Plans/11-usage-and-cost-reporting.md`.

## Verdict summary

Plan 11 is **complete for the agreed product scope**.

The original 2026-07-03 finding said Plan 11 was not implemented. That finding is superseded by the
current code and the authoritative `report.md`: usage ingestion, campaign attribution, rollups, scheduled
recompute, institution reporting, SMS late-price backfill, and group usage reporting now exist.

Remaining plan-spec items are not blockers:
- Voice/email cost remains `$0` by product Option B because the providers do not emit per-send prices and
  no approved business rate card exists.
- `usage_budgets`/budget caps are dropped by the no-caps product decision.
- Dashboards are Plan 08 frontend work consuming the shipped usage APIs.
- Alarms and deeper RLS/integration coverage are operational hardening.

## Current implemented state

1. **Usage event model + RLS:** `UsageEvent` exists with channel, direction, quantity metrics, cost fields,
   idempotency key, and workflow attribution.
2. **Usage metering service:** `UsageMeteringService.record` performs idempotent ingestion and backfills NULL
   SMS cost/segments when a later Twilio callback carries price data.
3. **All-channel ingestion:** SMS status callbacks record segments/price where Twilio provides them; email sends
   record `emails=1`; Retell post-call webhook records voice minutes/dials.
4. **Campaign attribution:** `usage_events.workflow_run_id` and `workflow_id` support per-campaign reporting.
5. **Rollups:** `usage_cost_rollups` plus `UsageRollupService` aggregate daily usage/cost for location and
   institution scopes.
6. **Scheduled recompute:** infra defines the `RecomputeUsageRollup` scheduled admin task on a 15-minute cadence.
7. **Institution reporting API:** `/institution/usage/summary` and `/institution/usage/by-campaign` read the
   rollups with RLS enforcement.
8. **Group reporting API:** `/api/group/usage-summary` aggregates cost/usage across member institutions for
   `GROUP_ADMIN`, backed by the group RLS branch on `usage_cost_rollups`.

## Residual decisions

- **Cost estimation fallback:** deliberately not built. Exact usage counts are present; estimated dollar values
  need approved business rates before they should appear in product.
- **Budgets:** dropped with the no-caps/no-limits product decision.
- **Frontend dashboards:** not part of Plan 11 closeout; tracked in Plan 08.
- **Alarms/tests:** recommended hardening, not required for feature completion.

## Confidence

High. Current source contains the models, migrations, services, APIs, scheduled infra, and unit tests referenced
above. This file is now aligned with `report.md` and `outbound-followups-and-gaps.md`.
