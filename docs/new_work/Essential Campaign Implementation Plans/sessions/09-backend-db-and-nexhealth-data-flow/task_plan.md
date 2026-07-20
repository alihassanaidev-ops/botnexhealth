# Task Plan: 09 Backend DB And NexHealth Data Flow

## Goal

Implement the Backend DB And NexHealth Data Flow plan after earlier plans are complete.

## Current Phase

Plan 09 complete: all prod-readiness slices implemented.

## Phases

- **Patient webhook support:** complete
- **Sync-status support:** complete
- **Backfill/reconciliation jobs:** complete
- **PMS capability gating:** complete
- **Webhook durability/ops hardening:** complete

## Key Questions

1. Should patient webhooks trigger workflows directly? No. They refresh contact/patient projection only; appointment, recall, callback, or future treatment/insurance projections decide enrollment.
2. Should patient subscriptions use a separate NexHealth endpoint immediately? No. Keep current endpoint compatible by dispatching patient events on the existing receiver URL, while also exposing a dedicated `/patients` route for future endpoint separation.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Patient webhooks refresh projections but do not enroll campaigns. | Prevents patient profile edits from creating outreach without a campaign-specific trigger. |
| Existing NexHealth receiver URL accepts patient events. | Current provider endpoint is already registered; this avoids needing an immediate endpoint migration while adding patient subscriptions. |
| Sync-status webhooks update health but do not replace polling. | NexHealth recovery webhooks do not reliably report green-to-red failures, so `GET /sync_status` polling remains required. |
| Known PMS read-down blocks appointment-triggered launch/runtime sends when projection is stale. | Appointment outreach is unsafe if local projection is stale and PMS reads are explicitly unhealthy. |
| Patient backfill/reconciliation refreshes contact projections only. | Patient rows maintain identity/contact freshness; they do not directly enroll campaigns. |
| Patient watermarks are separate from appointment watermarks. | Appointment repair and contact freshness have different API endpoints, rates, and update cadence. |
| Keep existing basic recall scanner in this slice. | Recall polling already exists; durable recall working-set and unsupported-PMS UX belong with PMS capability gating. |
| PMS capability support is read from NexHealth supported-API matrices and latest sync-status PMS identity. | Avoids hard-coding one PMS assumption per clinic and lets template/checklist/runtime gates use the same source of truth. |
| Partial or unknown PMS support blocks gated campaign templates. | Recall/treatment/writeback flows should fail closed unless the selected clinic's PMS support is known. |
| Claimed NexHealth webhook processor failures are dead-lettered and acknowledged. | Prevents NexHealth endpoint deactivation from repeated 500s while preserving repair/debug payloads. |
| Raw NexHealth webhook envelopes are encrypted and retained short-term. | Operators can debug real delivery issues without keeping PHI-bearing raw payloads indefinitely. |
