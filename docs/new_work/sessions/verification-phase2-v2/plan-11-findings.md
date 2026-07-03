# Plan 11 — Usage & Cost Reporting — Verification Findings

Audit date: 2026-07-03. Branch: `ali/phase-2`.
Method: graphify orient → grep/glob across `src/`, `alembic/`, `tests/`, `nexus-dashboard-web/`.
Plan file: `docs/new_work/Implementation Plans/11-usage-and-cost-reporting.md`.

## Verdict summary
Plan 11 is **NOT implemented**. Neither the metering ingestion layer nor the
rollup/reporting/dashboard layers exist. The only building block present is a raw
voice-duration field (`call_duration_seconds`) on `Call`, which is not wired into any
usage model.

## Deliverables extracted from the plan
1. `usage_events` model (unified voice/sms/email, inbound/outbound, source refs, quantity metrics minutes/dials/segments/emails, cost_amount/currency, idempotency key) + RLS + migration.
2. `usage_cost_rollups` model (scope key × date × channel × direction, summed metrics/cost) — the contract Part 8 depends on.
3. Optional `usage_budgets` model (read side of Part 12).
4. `UsageMeteringService` — idempotent ingestion hooked into Retell post-call, Twilio status callback, Resend webhook handlers.
5. `UsageRollupService` — daily/incremental hierarchical rollup location → institution → DSO group.
6. `UsageReportingService` + APIs — summary/trend/per-campaign spend, budget status.
7. Dashboard UI wiring in `nexus-dashboard-web/` (via Part 8).
8. Cost estimation fallback (Retell $0.005/dial etc.) with `estimated` flag.
9. Metrics/alarms for ingestion lag, rollup failures, untagged usage.

## Evidence — MISSING (all core deliverables)

### No models
- `src/app/models/__init__.py` (full file read): registers 0 usage models. No `UsageEvent`,
  `UsageCostRollup`, `UsageBudget`. Also no `WorkflowVoiceAttempt` / `WorkflowEmailAttempt` /
  SMS-attempt model (the plan's "Existing System Context" assumed these from Parts 3/4/5 — they
  do not exist as models).
- Grep `usage_event|UsageMetering|usage_cost_rollup|usage_budget|UsageRollup|UsageReporting`
  (case-insensitive, whole repo): matches ONLY in three plan docs
  (`11-...md`, `12-compliance-and-consent.md`, `08-...analytics-ui.md`). Zero source hits.

### No migration
- `alembic/versions/*.py` (23 files listed): no `usage_events`, `usage_cost_rollups`, or
  `usage_budgets` migration. Latest Phase-2 migrations are `20260702_auto_workflow_core.py`,
  `20260703_consent_channel.py`, `20260703_institution_provisioning.py`,
  `20260703_outbound_halt.py` — none touch usage/cost.

### No ingestion hooks (no capture of provider billing signals)
- SMS dispatch: Grep `num_segments|NumSegments|price|Price|segments|cost` in
  `sms_service.py` / `sms.py` → **No matches**. Twilio `NumSegments`/`Price` from status
  callbacks are not captured. `src/app/models/sms_history_log.py` has no segments/price/cost
  fields (Grep → No matches). The plan assumed Part 4 added `provider_segments`/`price_amount`
  on SMS attempts — not present.
- Email dispatch: no cost/usage capture; no email-attempt model with cost fields.
- Voice: only `src/app/models/call.py:218` `call_duration_seconds` exists — a raw duration
  signal, but NOT fed into any usage-event pipeline. No dials/minutes metering, no Retell price
  ingestion. `PostCallService` does not emit usage events (no usage refs in source).
- Step dispatcher (`src/app/services/automation/step_dispatcher.py`): only hit for
  `duration_seconds` is a timer-delay computation (L271), unrelated to metering.

### No rollup job / service
- No `UsageRollupService`. The `call_metrics_daily` rollup (`src/app/services/dashboard_rollup.py`,
  `alembic/versions/20260513_call_metrics_daily.py`) exists as the *pattern* the plan says to
  mirror, but no usage rollup was built on it.

### No reporting API
- `src/app/api/routes/*.py` (29 route files): no usage/cost route. `dashboard.py` and `group.py`
  serve call-volume/KPI aggregates only; no channel spend, per-campaign spend, or budget status.

### No dashboard UI
- Grep `usage|metering|cost_rollup|rollup` in `nexus-dashboard-web/`: only incidental matches in
  `package-lock.json`, `use-step-up.tsx`, `ui/select.tsx`. No usage/cost dashboard component.

### No tests
- Grep `usage|metering|UsageEvent|rollup|cost` in `tests/`: matches are for the unrelated
  `call_metrics` dashboard rollup (`test_dashboard_rollup_sql_shape.py`,
  `test_scheduled_jobs.py`, `test_institution_dashboard.py`) and coincidental "cost"/"usage" in
  auth/password tests. Zero usage-metering tests. None of the plan's validation-strategy tests
  (idempotent ingestion, late price update, hierarchical rollup math, RLS on usage tables,
  end-to-end attempt→event→rollup→API) exist.

## Ingestion vs rollup/dashboard separation (as requested)
- **Metering ingestion:** NOT shipped with any channel (04 SMS / 05 email / 10 provisioning).
  No provider cost/segment/minute signals are captured on any attempt/history record. The scope
  sequence expected ingestion to land with the first channel — it did not.
- **Rollups / dashboards:** NOT shipped (expected absent per Phase-6 sequencing — confirmed).

## Scope alignment
Scope §9.4 (usage & cost in analytics UI) and §12 (usage metering, inbound+outbound) are
entirely unaddressed. Part 8's dependency on `usage_cost_rollups` and Part 12's dependency on
current-period usage are both unmet — those consumers have no data source.

## Confidence
High. Multiple independent searches (graphify + grep across models, migrations, services, routes,
frontend, tests) all converge on zero implementation.
