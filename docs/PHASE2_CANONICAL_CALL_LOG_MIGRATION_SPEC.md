# Phase 2 Bridge Spec: Canonical Postgres Call Lifecycle Ownership

## Objective

Migrate from the current hybrid model (Retell + GHL as dashboard call source) to canonical backend-owned call lifecycle records in Postgres, while preserving backward compatibility during rollout.

## Current Phase (Implemented)

- Retell tool calling remains primary for live call actions.
- GHL remains source for dashboard call list.
- Webhook processing is idempotent by `call_id` + `event_type`.
- Backend emits freshness events so frontend can pull latest GHL data on demand.

## Phase 2 Target

1. Postgres `call_logs` as source of truth.
2. Dashboard reads call timeline from Postgres (GHL optional mirror only).
3. Full SOP-aligned lifecycle ownership:
   - call start context
   - function-call trail
   - call end outcomes/tags
   - callback queue state
   - audit-grade immutable event trail

## Proposed Data Model Additions

- `call_logs`
- `call_function_invocations`
- `call_tags`
- `callback_queue_items`
- `call_processing_outbox` (optional for async downstream deliveries)

## Migration Strategy

1. **Dual-write**: Keep existing GHL sync and add Postgres writes from webhook pipeline.
2. **Read-toggle**: Feature flag dashboard per tenant to read Postgres vs GHL.
3. **Parity checks**: Compare GHL and Postgres counts/tags for selected tenants.
4. **Cutover**: Switch default dashboard reads to Postgres.
5. **Decommission**: Remove GHL dependency for call list once parity is stable.

## Acceptance Gate For Phase 2 Start

- Hybrid phase stable for at least one full release cycle.
- Duplicate webhook side effects at zero across monitored tenants.
- Freshness event channel stable in production.
- Tenant isolation and idempotency test suite green in CI.

