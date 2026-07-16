# Essential 7 - Audience Preview And Simple Segmentation Implementation Plan

## What Needs To Be Built

Build an audience preview and simple segmentation layer so clinic admins can see who a campaign will contact, who will be excluded, and why before launch.

The first version should cover practical dental filters:

- no future appointment
- overdue recall
- last visit/date window where available
- appointment type
- provider
- location
- preferred language
- consent/channel availability
- do-not-contact/suppression
- contacted recently/frequency cap

## Existing System Context

The backend already has:

- Contact model and patient-facing communication primitives.
- Manual enrollment and bulk enrollment APIs.
- Appointment working set for upcoming appointment triggers.
- NexHealth appointment backfill/reconciliation.
- Recall capability is planned/partially documented in earlier plans.
- Consent/suppression/quiet-hours services.

The frontend already has:

- Manual contact enrollment dialog.
- Campaign detail page.
- Existing patient/contact tables and filters elsewhere in the app.

Current gap:

- There is no audience builder or preview surface.
- Bulk/manual enrollment does not provide a clinic-friendly explanation of inclusions/exclusions.
- Segmentation is not yet a reusable backend contract for templates, launch checklist, and analytics.

## Existing Components To Reuse

- Contact list/query patterns.
- Manual/bulk enrollment endpoints.
- Compliance gate and suppression services.
- NexHealth appointment working set.
- Launch checklist.
- Campaign template metadata.

## New Components Required

### Data Model

- `campaign_audience_definitions`
  - workflow/template ID
  - segment JSON
  - exclusion JSON
  - created/updated by

- `campaign_audience_previews`
  - preview request metadata
  - counts by included/excluded reason
  - expires quickly

Preview row-level patient samples should be generated on demand or stored with short retention only if necessary.

### Segment DSL

Support a constrained JSON structure:

- filters:
  - `has_no_future_appointment`
  - `recall_due_before`
  - `last_visit_before`
  - `appointment_type_id_in`
  - `provider_id_in`
  - `location_id_in`
  - `preferred_language_in`
  - `contact_channel_available`

- exclusions:
  - `no_consent`
  - `do_not_contact`
  - `suppressed`
  - `contacted_within_days`
  - `already_enrolled_active`
  - `already_booked`
  - `missing_required_merge_context`

### Backend APIs

- `POST /automation/workflows/{workflow_id}/audience/preview`
- `PUT /automation/workflows/{workflow_id}/audience`
- `GET /automation/workflows/{workflow_id}/audience`
- `POST /automation/workflows/{workflow_id}/audience/enroll`

### Frontend

- Audience tab or setup step.
- Filter controls for the simple segment DSL.
- Preview count cards.
- Exclusion breakdown.
- Sample patient list with masked phone/email by default.
- Commit/enroll button gated by launch checklist.

## End-To-End Implementation Approach

1. Define segment DSL and validation schema.
2. Build backend audience preview service returning counts and masked samples.
3. Implement consent/suppression/frequency-cap exclusion reasons.
4. Add simple filters backed by local contacts and appointment working set.
5. Add recall filters once recall working set exists.
6. Add audience definition persistence per workflow.
7. Add frontend audience builder and preview UI.
8. Wire preview summary into launch checklist.
9. Add enroll-from-preview path with idempotency and revalidation.
10. Add audit logging for audience preview and enrollment commits.

## Timeline

Estimated duration: 3 weeks.

- Days 1-3: segment DSL, persistence, and backend preview foundation.
- Days 4-7: contact/appointment/consent/frequency exclusion logic.
- Days 8-10: frontend filter controls, count cards, and masked sample table.
- Days 11-12: enroll-from-preview and launch checklist integration.
- Days 13-15: recall filter integration, tests, and staging QA.

## Architecture Decisions

- Keep v1 segmentation intentionally constrained. No arbitrary SQL-like builder.
- Always compute exclusions alongside inclusions.
- Use local disposable projections for audience discovery, but perform live revalidation before send/action.
- Treat preview as advisory until enrollment commit; commit must recheck consent and campaign status.

## Technical Considerations

- Audience preview can be PHI-heavy. Mask samples and avoid storing raw preview results long term.
- Counts can change between preview and launch because patients book, opt out, or change status.
- Large audiences need pagination and count-only fast paths.
- Patient matching from CSV/manual imports should reuse the same exclusion reason vocabulary.
- Frequency caps must consider all outbound channels, not just the campaign.

## Dependencies

- NexHealth data-flow plan for appointment/recall/treatment working sets.
- Rich merge fields for missing-context exclusions.
- Launch checklist.
- Patient response handling for contacted-recently and already-active-run signals.
- Basic analytics for audience-to-outcome conversion later.

## Edge Cases

- Patient belongs to multiple locations.
- Patient has future appointment at another location.
- Contact has no phone but email is available.
- Same phone number maps to multiple contacts.
- Recall endpoint unsupported for a PMS.
- Preview includes 400 patients, but 20 opt out before launch.
- CSV import includes patients not found in local contacts.

## Risks

- Over-targeting patients without reliable consent.
- Preview results become stale and create false confidence.
- Segment DSL grows into an unmaintainable query language.
- PHI exposure in previews.

## Validation Strategy

- Unit tests for every filter and exclusion reason.
- Integration tests for tenant/location scoping.
- Preview-to-enrollment idempotency tests.
- Frontend tests for filter controls, exclusion breakdown, and masked samples.
- Manual staging test with recall, no-future-appointment, consent, and frequency-cap filters.

## Deployment Considerations

- Ship preview read-only before enroll-from-preview.
- Add maximum audience size and CSV row limits for v1.
- Feature-flag recall/treatment filters until data-flow support is live.
- Audit export/reveal behavior if unmasked samples are allowed.

## Future Extensibility

- Saved audience segments.
- DSO-level reusable segments.
- Advanced boolean segment builder.
- Revenue/treatment-plan filters.
- Lookalike or campaign-performance-based recommendations.
