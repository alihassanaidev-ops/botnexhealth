# Task Plan: 05 Dental-Specific Campaign Templates

## Goal

Implement the Dental-Specific Campaign Templates plan after earlier plans are complete.

## Current Phase

Complete

## Phases

- **Status:** complete
- Read Plan 05 and binding 09-12 decision/context docs.
- Use graphify to find campaign template/workflow/merge/frontend surfaces.
- Add backend dental template metadata and priority templates.
- Wire instantiate endpoint to apply guided setup data and voice-agent substitution.
- Update frontend API types and template picker guided setup flow.
- Add focused backend/frontend tests.
- Run verification and update status/session docs.

## Key Questions

1. No open product/architecture questions. The only ambiguous voice-template point was resolved in implementation by requiring guided setup to supply the clinic Retell agent ID.

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Do not add a workflow settings JSON column for template metadata | Existing workflow persistence supports category/description/content classification and immutable definitions; richer template metadata can remain in the template API contract. |
| Use metadata-only PMS gating for recall/treatment templates in Plan 05 | Runtime capability and audience preview services are later plans; this keeps Plan 05 backward-compatible while exposing the needed contract. |
| Make the callback automation template voice-first but non-clonable without setup | Satisfies callback/voice scope without hardcoding a clinic-specific Retell agent. |
