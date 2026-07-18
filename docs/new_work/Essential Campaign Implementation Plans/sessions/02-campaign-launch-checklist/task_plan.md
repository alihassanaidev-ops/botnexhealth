# Task Plan: 02 Campaign Launch Checklist

## Goal

Build a launch readiness checklist that combines workflow validation, merge-field readiness, channel provisioning, compliance, audience estimates, NexHealth readiness, handoff checks, send volume, and cost estimates before campaign launch.

## Current Phase

Complete

## Phases

### Phase 1: Requirements And Discovery
- [x] Read implementation plan
- [x] Confirm Plan 01 committed
- [x] Map existing backend/frontend publish and readiness code
- **Status:** complete

### Phase 2: Backend Checklist Contract
- [x] Add checklist service and response schema
- [x] Compose validation, channel, compliance, audience, NexHealth, handoff, and estimate checks
- [x] Add GET and preview endpoints
- **Status:** complete

### Phase 3: Frontend Checklist Surface
- [x] Add API client/types
- [x] Add builder checklist panel
- [x] Update launch confirmation dialog to show blockers/warnings
- **Status:** complete

### Phase 4: Tests And Verification
- [x] Add backend unit/API tests
- [x] Add frontend tests
- [x] Run focused checks
- [x] Update Graphify
- **Status:** complete

### Phase 5: Handoff
- [x] Mark plan complete
- [x] Provide commit message and description
- **Status:** complete

## Key Questions

1. What current action maps to "activation" in this app: publish, resume, or both?
2. What data can be safely estimated before the audience preview/segmentation plan exists?

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Implement Plan 02 after Plan 01 commit | User requested one-by-one implementation and confirmed Plan 01 was committed. |
| Keep checklist read-only in this slice | Deployment notes say ship read-only first, then block activation once production override/audit policy exists. |
| Treat missing audience/cost as explicit `unknown` | Audience preview and segmentation are Plan 07 dependencies; inventing counts would mislead admins. |
| Treat recall audience as blocked for now | Recall is the only automated broad trigger in this schema and depends on the later audience adapter. |
