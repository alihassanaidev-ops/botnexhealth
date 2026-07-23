# Essential Campaign Implementation Status

Last updated: 2026-07-24 02:05 PKT

## Rules

- Implement plans one by one in numeric order.
- Ask before making a product or architecture decision that is not settled in the plan docs.
- Update this file and the active plan session files as work progresses.
- After each plan is implemented and verified, provide the commit message and description for the user to run.

## Plan Status

| Plan | Title | Status | Session |
|------|-------|--------|---------|
| 01 | Rich Dental Merge Fields | complete | sessions/01-rich-dental-merge-fields |
| 02 | Campaign Launch Checklist | complete | sessions/02-campaign-launch-checklist |
| 03 | Campaign Overview And Run Progress | complete | sessions/03-campaign-overview-and-run-progress |
| 04 | Patient Response Handling | complete | sessions/04-patient-response-handling |
| 05 | Dental-Specific Campaign Templates | complete | sessions/05-dental-specific-campaign-templates |
| 06 | Basic Outcome Analytics | complete | sessions/06-basic-outcome-analytics |
| 07 | Audience Preview And Simple Segmentation | complete | sessions/07-audience-preview-and-simple-segmentation |
| 08 | Callback Trigger And Voice Outcome UI Exposure | complete | sessions/08-callback-trigger-and-voice-outcome-ui-exposure |
| 09 | Backend DB And NexHealth Data Flow | complete | sessions/09-backend-db-and-nexhealth-data-flow |
| 10 | NexHealth Webhooks And Data Summary | pending | sessions/10-nexhealth-webhooks-data-summary |
| 11 | Concrete Campaign Build Plan | pending | sessions/11-concrete-campaign-build-plan |
| 12 | Campaign Implementation Decisions | pending | sessions/12-campaign-implementation-decisions |

## Current Plan

Plan 09: Backend DB And NexHealth Data Flow complete

## Add-On Work

| Work | Status | Session |
|------|--------|---------|
| GoTracker Synchronizer PMS adapter | adapter/config/UI/webhook receiver/subscription automation complete; live Synchronizer acceptance pending staging product-key test | sessions/gotracker-synchronizer-pms-adapter |
| Dental outbound workflow primitives | appointment-type trigger filtering, appointment-relative waits, Retell outcome-ready voice flow, and local patient status recording complete; real phone ringing blocked only by provider international dialing permission during local QA | sessions/dental-outbound-workflow-primitives |

## Prod-Readiness Slices

| Slice | Status | Notes |
|-------|--------|-------|
| Patient webhook support | complete | Subscribes to `patient_created`/`patient_updated`, refreshes contact identity, stores `patient_working_set`, and accepts patient events on the existing NexHealth receiver URL. |
| Sync-status support | complete | Subscribes to `sync_status_read_change`/`sync_status_write_change`, polls `GET /sync_status`, stores per-location PMS health, and surfaces read/write health in launch/runtime readiness. |
| Backfill/reconciliation jobs | complete | Appointment repair already existed; added patient/contact backfill and reconciliation via `GET /patients`, patient watermarks, and scheduled repair. Basic recall polling exists; recall working-set/capability gating remains in the PMS capability slice. |
| PMS capability gating | complete | Added PMS capability evaluation from NexHealth supported-API matrices, gated recall/treatment templates by selected location, surfaced checklist blockers, and gated confirmation writeback. |
| Webhook durability/ops hardening | complete | Stores encrypted raw/redacted NexHealth webhook envelopes short-term, dead-letters claimed processor failures, strengthens dedupe fallbacks, and marks inactive/stale subscriptions failed. |

## Verification Log

| Plan | Verification | Status |
|------|--------------|--------|
| 01 | Backend focused pytest, frontend focused vitest, frontend build, ruff, eslint | passed with noted warnings |
| 02 | Backend focused pytest, frontend focused vitest, frontend build, ruff, touched-file eslint, Graphify update | passed with noted warnings |
| 03 | Backend focused pytest, frontend focused vitest, frontend build, ruff, touched-file eslint | passed with noted warnings |
| 04 | Backend focused pytest, voice integration pytest, frontend focused vitest, frontend build, ruff, eslint | passed with noted warnings |
| 05 | Dental template pytest, validation/checklist pytest, frontend focused vitest, frontend build, ruff, touched-file eslint | passed with noted warnings |
| 06 | Campaign analytics pytest, RBAC route matrix pytest, frontend focused vitest, backend ruff, frontend lint, backend import smoke | passed with noted warnings |
| 07 | Audience service/checklist/projection/RBAC pytest, backend ruff, frontend CampaignDetail vitest, frontend eslint, frontend build | passed with noted warnings |
| 08 | Callback/voice focused backend pytest, backend ruff, frontend workflow vitest, touched-file eslint, frontend build | passed with noted warnings |
| 09 patient webhook slice | NexHealth webhook/projection/subscription pytest, touched-file backend ruff | passed |
| 09 sync-status slice | NexHealth subscription/sync-status/webhook/checklist/revalidation/RBAC pytest, touched-file backend ruff, Alembic heads | passed with noted warnings |
| 09 backfill/reconciliation slice | NexHealth patient/appointment sync pytest, adapter pytest, Plan 09 recall pytest, touched-file backend ruff, Alembic heads | passed with noted warning |
| 09 PMS capability gating slice | Capability/template/checklist pytest, frontend workflow API/template picker vitest, touched-file backend ruff, frontend eslint | passed with noted warnings |
| 09 webhook durability/ops hardening slice | NexHealth projection/webhook/subscription/retention pytest, touched-file backend ruff, Alembic heads | passed with noted warning |
| GoTracker Synchronizer PMS adapter | GoTracker adapter/factory/webhook/RBAC pytest, retention pytest, backend compile smoke, ruff, Alembic heads, frontend production build | passed with noted warnings |
| Dental outbound workflow primitives | Focused automation pytest, frontend workflow vitest, frontend production build, ruff, Alembic heads, RLS coverage check, local Docker migration smoke, local GoTracker/NexHealth/automation pytest, local Retell ngrok QA through `create-phone-call` and webhook resume | passed except real call connect blocked by Retell/Twilio `No International Permission` for Pakistan destination |
