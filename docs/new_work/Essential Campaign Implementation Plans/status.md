# Essential Campaign Implementation Status

Last updated: 2026-07-21 00:35 PKT

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
| 09 | Backend DB And NexHealth Data Flow | in progress | sessions/09-backend-db-and-nexhealth-data-flow |
| 10 | NexHealth Webhooks And Data Summary | pending | sessions/10-nexhealth-webhooks-data-summary |
| 11 | Concrete Campaign Build Plan | pending | sessions/11-concrete-campaign-build-plan |
| 12 | Campaign Implementation Decisions | pending | sessions/12-campaign-implementation-decisions |

## Current Plan

Plan 09: Backend DB And NexHealth Data Flow prod-readiness slices

## Prod-Readiness Slices

| Slice | Status | Notes |
|-------|--------|-------|
| Patient webhook support | complete | Subscribes to `patient_created`/`patient_updated`, refreshes contact identity, stores `patient_working_set`, and accepts patient events on the existing NexHealth receiver URL. |
| Sync-status support | pending | Subscribe to read/write status events, poll `GET /sync_status`, and surface PMS health in checklist/runtime readiness. |
| Backfill/reconciliation jobs | pending | Appointment/patient reconciliation, missed-webhook repair, recall polling if recall campaigns remain in scope. |
| PMS capability gating | pending | Capability model/service for recall, treatment plan, procedure, writeback, and unsupported feature UX. |
| Webhook durability/ops hardening | pending | Raw encrypted payload retention, dead-letter/retry processing, stronger endpoint inactive monitoring. |

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
