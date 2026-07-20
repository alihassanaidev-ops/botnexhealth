# Task Plan: 09 Backend DB And NexHealth Data Flow

## Goal

Implement the Backend DB And NexHealth Data Flow plan after earlier plans are complete.

## Current Phase

Prod-readiness slice 2 complete: sync-status support.

## Phases

- **Patient webhook support:** complete
- **Sync-status support:** complete
- **Backfill/reconciliation jobs:** pending
- **PMS capability gating:** pending
- **Webhook durability/ops hardening:** pending

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
