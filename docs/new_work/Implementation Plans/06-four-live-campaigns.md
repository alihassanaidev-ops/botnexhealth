# Part 6 - Four Live Campaigns Implementation Plan

## What Needs To Be Built

Build the four launch campaigns as configurable workflow templates on top of the workflow engine:

- Appointment Confirmation
- Appointment Reminder
- Overdue Patient Recall
- Sales Qualification

Each campaign must be tenant/location-scoped, cloneable into a clinic workflow, configurable through the builder/management UI, validated before publish, and executable end to end through the workflow runtime and outbound channel actions.

## Existing System Context

The platform currently has:

- Inbound Retell booking/rescheduling/cancellation functions.
- Live NexHealth patient lookup, slot search, and booking through the PMS adapter.
- Contacts created from inbound calls.
- SMS send/log/suppression primitives.
- Email notification/template primitives.
- Callback queue and call statuses.
- Per-location timezone, operating hours, breaks, provider buffers, appointment types, providers, operatories, and PMS binding.

Current gaps:

- No workflow templates exist.
- No launch campaign definitions exist.
- No appointment/recall trigger projections exist yet.
- No campaign-specific outcomes/analytics exist.
- Sales Qualification's true inbound lead intake is deferred in the scope/gap analysis, so v1 can only support manual/CSV/new-contact variants until lead intake is built.

## Existing Components To Reuse

- Workflow engine, scheduler, and validation from Part 1.
- Visual builder and template configuration UI from Part 2.
- Outbound voice, SMS, and email actions from Parts 3, 4, and 5.
- Integration/data layer from Part 9 for appointment and recall triggers.
- Existing PMS booking flow and Retell handlers.
- Existing contact model and location access model.
- Existing SMS compliance and future cross-channel suppression framework.
- Existing audit/dead-letter/SSE/notification patterns.

## New Components Required

### Template Model

Add system templates through either database rows or checked-in seed definitions:

- `workflow_templates`
  - key: `appointment_confirmation`, `appointment_reminder`, `overdue_recall`, `sales_qualification`
  - name, description, category
  - default definition JSON
  - supported channels
  - content class and compliance metadata
  - minimum required provisioning/readiness
  - version and active flag

- `workflow_template_versions`
  - immutable system template snapshots
  - migration/seed history

### Campaign-Specific Configuration

Each cloned campaign should expose guided settings:

- enabled/disabled per location
- channel order
- send windows and quiet hours
- retry limits and delays
- message/call scripts or template copy
- PMS recheck rules
- attempt ceilings
- staff handoff behavior
- campaign-specific eligibility filters

### Outcome Mapping

Define normalized outcomes for analytics and branching:

- Confirmation: `confirmed`, `reschedule_requested`, `declined`, `no_response`, `skipped_cancelled`, `failed`
- Reminder: `delivered`, `responded`, `change_requested`, `no_response`, `skipped_cancelled`, `failed`
- Recall: `booked`, `not_interested`, `unreachable`, `attempts_exhausted`, `suppressed`, `failed`
- Sales Qualification: `qualified_booked`, `qualified_handoff`, `not_qualified`, `unreachable`, `deferred_no_lead_source`

## End-To-End Implementation Approach

1. Define canonical workflow template JSON for each campaign.
2. Add seed/migration mechanism for system templates.
3. Add template clone flow into tenant/location workflows.
4. Add campaign-specific validation rules on top of generic workflow validation.
5. Add default SMS/email copy and voice prompt variables using approved merge fields.
6. Add campaign-specific trigger adapters:
   - Appointment Confirmation: appointment time-offset trigger.
   - Appointment Reminder: appointment time-offset trigger.
   - Overdue Recall: recurring recall eligibility trigger plus manual/CSV enrollment.
   - Sales Qualification: manual/CSV/new-contact trigger until lead intake is implemented.
7. Add live PMS revalidation steps before appointment-related sends.
8. Add PMS write-back for confirmation status where NexHealth/PMS support permits it.
9. Add outcome mapping from channel attempts into campaign outcomes.
10. Add campaign analytics rollups used by Part 8 UI.
11. Add staging fixtures/test mode so templates can be validated without sending real outreach.

## Architecture Decisions

- Implement launch campaigns as templates, not special-case code paths. Campaign-specific behavior should live in template definitions plus reusable trigger/action handlers.
- Keep Sales Qualification launch boundary explicit. Without lead intake, it should support manual/CSV/new-contact enrollment only and mark external lead ingestion as a later dependency.
- Use PMS live revalidation for Confirmation and Reminder immediately before dispatch.
- Keep Recall eligibility based on NexHealth recall lists, not a full patient database.
- Keep campaign outcome mapping centralized so analytics, run detail, and branch decisions use consistent labels.

## Campaign Template Details

### Appointment Confirmation

- Trigger: appointment time-offset, usually 48-72 hours before appointment.
- Eligibility:
  - appointment exists in working set
  - appointment still active on live PMS recheck
  - not already confirmed
  - contact reachable and not suppressed
- Default flow:
  - revalidate appointment
  - send configured first channel
  - wait for response/outcome
  - if confirmed, write confirmation status to PMS where supported and exit
  - if reschedule requested, branch to AI voice rescheduling or staff handoff
  - if no response, retry/fallback channels within quiet hours
- Terminal outcomes: confirmed, reschedule requested, declined, no response, skipped, failed.

### Appointment Reminder

- Trigger: appointment time-offset, usually day-before and/or day-of.
- Eligibility:
  - appointment still active on live PMS recheck
  - not cancelled/rescheduled
  - contact reachable and not suppressed
- Default flow:
  - revalidate appointment
  - send reminder via selected channels
  - optionally accept confirm/change request
  - suppress remaining steps if patient responds
- Terminal outcomes: delivered, responded, change requested, skipped cancelled/rescheduled, no response, failed.

### Overdue Patient Recall

- Trigger: recurring recall eligibility scan and manual/CSV enrollment.
- Eligibility:
  - patient appears in recall eligibility working set or imported list
  - no known upcoming appointment after live PMS check
  - reachable and not suppressed
  - recall endpoint supported or list imported manually
- Default flow:
  - multi-touch drip across SMS/email/voice
  - AI voice can book using live availability
  - exit on booking, opt-out/suppression, not interested, or attempt ceiling
- Terminal outcomes: booked, not interested, unreachable, attempts exhausted, suppressed, failed.

### Sales Qualification

- Trigger:
  - v1: manual enrollment, CSV enrollment, new contact from existing platform events where consent exists
  - later: inbound lead/webhook after lead intake is built
- Eligibility:
  - contact/lead has consent provenance
  - reachable and not suppressed
  - booking-capable location where qualification should book directly
- Default flow:
  - AI call or SMS/email first touch depending on config
  - qualify intent/service
  - if qualified, book through live NexHealth availability
  - if ambiguous/hot, notify staff
  - if not qualified, record outcome and exit
- Terminal outcomes: qualified booked, qualified handoff, not qualified, unreachable, failed.

## Technical Considerations

- Template definitions must use only runtime primitives supported by the engine.
- Campaign defaults should be conservative: low attempt counts, quiet hours required, clear staff
  fallback, and defaults that respect the Part 12 **frequency cap** (≤1/day, ≤3/week combined
  calls+texts per patient/provider) — this is a launch compliance control, not just UX (Finding 3).
- **Template scope: this plan owns the four launch-campaign templates only.** The **AI Callback
  workflow template** referenced by the Part 2 builder palette and the Part 7 automation is
  defined in **Part 7** (it enrolls callback rows into that template), not here. Keep the template
  set consistent across Parts 2/6/7 so the builder palette matches what actually exists.
- Confirmation status write-back must be capability-checked per PMS/NexHealth endpoint support.
- Recall must handle PMSs without recall endpoint support and surface that readiness state.
- CSV/manual enrollment must validate consent proof before any outbound step.
- Sales Qualification should not imply cold outreach; consent and source must be explicit.
- Templates should include sample data for preview/test runs.

## Dependencies

- Workflow engine and builder.
- Outbound voice/SMS/email.
- Integration/data layer for appointment and recall triggers.
- Per-tenant messaging provisioning.
- Campaign management/progress/analytics UI.
- Cross-channel compliance/suppression framework.
- PMS capability mapping for confirmation write-back and recall support.

## Edge Cases

- Appointment is cancelled after trigger enrollment but before first send.
- Appointment is rescheduled to a time that changes the trigger offset.
- Patient confirms on SMS after voice fallback was already queued.
- Recall patient books through staff outside the workflow.
- Recall eligibility disappears on next PMS pull.
- CSV import includes duplicate contacts or contacts already in active runs.
- Sales Qualification contact is already an existing patient with future appointment.
- Location has no PMS and cannot book directly.
- A campaign template is updated while clinics have older cloned versions.

## Risks

- Treating templates as code-like special cases can undermine the dynamic workflow engine.
- Sales Qualification can be over-scoped if lead intake is treated as launch-ready despite being deferred.
- Campaign copy can cross into marketing content without correct consent/content-class validation.
- PMS capability differences can make confirmation write-back or recall inconsistent across clinics.
- Too many default attempts can create compliance and patient-experience risk.

## Validation Strategy

- Unit tests validating each system template against workflow schema.
- Unit tests for campaign-specific validation rules.
- Integration tests cloning templates into tenant workflows.
- Dry-run tests for all four campaign definitions.
- End-to-end staging tests:
  - appointment confirmation with PMS recheck and outcome mapping
  - appointment reminder skip on cancelled appointment
  - recall enrollment from eligibility row
  - sales qualification manual enrollment with booking/handoff branch stub
- RLS tests for template clones and runs.
- Regression tests ensuring template updates do not mutate published tenant versions.

## Deployment Considerations

- Seed system templates after workflow tables exist.
- Hide campaign activation until required channels and data triggers are ready.
- Roll out one campaign template at a time in staging.
- Pilot Appointment Reminder or Confirmation first because they have clearer trigger data once appointment projection exists.
- Mark Sales Qualification as limited/manual until lead intake is implemented.
- Add metrics per template: activations, validation failures, enrollments, completions, skips, failures.

## Future Extensibility

- Additional templates for cancellations, reactivation, treatment-plan follow-up, and no-show recovery.
- Template marketplace or DSO-level template libraries.
- A/B testing variants.
- Lead intake/web-form integration for full Sales Qualification.
- PMS-specific template variants where capability differences require them.
