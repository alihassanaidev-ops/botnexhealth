# Task Plan: 07 Audience Preview And Simple Segmentation

## Goal

Implement the Audience Preview And Simple Segmentation plan after earlier plans are complete.

## Current Phase

Complete

## Phases

- **Status:** complete
- Discovery and decision-doc skim complete.
- Backend persistence and constrained segment DSL complete.
- Backend preview/exclusion/enroll service complete.
- Launch checklist audience summary integration complete.
- Frontend Audience tab and API client complete.
- Focused backend/frontend verification complete.

## Key Questions

1. No open product/architecture decision blocked Plan 07.
2. Recall due, preferred language, and richer patient fields remain capability/projection gated until patient/recall working sets are implemented.
3. Projected cost remains unavailable because channel pricing configuration is not yet modeled.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Keep v1 filters constrained to explicit JSON fields. | Matches Plan 07 and avoids an arbitrary query builder. |
| Use short-lived preview summary rows and on-demand masked samples. | Reduces PHI retention risk while supporting preview-to-enroll idempotency. |
| Use the latest unexpired preview for launch checklist audience/send-volume estimates. | Avoids expensive preview recomputation in checklist reads and makes stale estimates visible through `expires_at`. |
