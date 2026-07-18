# Task Plan: 01 Rich Dental Merge Fields

## Goal

Expand campaign merge fields into a backend-owned dental catalog with trigger/channel availability, renderer support, validation, dry-run samples, and frontend grouped filtering.

## Current Phase

Complete

## Phases

### Phase 1: Requirements And Discovery
- [x] Read implementation plan
- [x] Read campaign decisions
- [x] Map existing backend/frontend workflow code
- **Status:** complete

### Phase 2: Backend Catalog And Renderer
- [x] Add typed catalog service/model
- [x] Preserve existing static tokens
- [x] Add dental context-backed tokens
- [x] Update merge-field API contract compatibly
- **Status:** complete

### Phase 3: Context, Validation, And Dry Run
- [x] Add normalized merge context building
- [x] Extend trigger/channel token validation
- [x] Extend dry-run samples
- **Status:** complete

### Phase 4: Frontend Picker And Preview
- [x] Group fields in picker
- [x] Filter by selected trigger/channel
- [x] Use backend metadata for preview and unknown-token checks
- **Status:** complete

### Phase 5: Testing And Handoff
- [x] Add focused backend tests
- [x] Add focused frontend tests
- [x] Run relevant checks
- [x] Resolve default-template link-token decision
- [x] Provide commit message and description
- **Status:** complete

## Key Questions

1. Should shipped default templates include booking/confirmation link tokens before the per-run link generator exists? No; defer until per-run link generation exists.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Implement plan 01 first | Plans are numbered and user requested one-by-one implementation. |
| Use backend catalog as source of truth | Required by plan and preserves frontend fallback as a compatibility layer only. |
| Emit warnings for unavailable/PHI-heavy merge fields | Plan asks for warning behavior and existing content compliance remains the publish-blocking PHI layer. |
| Defer booking/confirmation link tokens in default templates | Prevents shipped templates from rendering blank links before per-run link generation exists. |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
