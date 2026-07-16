# Essential 5 - Dental-Specific Campaign Templates Implementation Plan

## What Needs To Be Built

Expand the campaign template library from generic starter workflows into a dental campaign library with guided defaults, required merge fields, compliance metadata, outcome mappings, launch checklist expectations, and sample data.

The template library should make the product feel purpose-built for dental operations, not like a blank automation canvas.

## Existing System Context

The backend already has:

- `src/app/services/automation/campaign_templates.py`.
- Four checked-in templates:
  - Appointment Reminder 24h
  - Appointment Confirmation 48h
  - Recall Outreach 6-Month
  - Reactivation Campaign 18-Month
- Template listing and clone endpoints.
- Workflow definition schema for appointment, recall, manual, bulk, and callback triggers.

The frontend already has:

- Campaign template page.
- Template clone/create flow.
- Builder for editing cloned workflows.

Current gap:

- Existing templates are SMS-heavy and generic.
- There are no templates for no-show recovery, cancellation rebooking, callback automation, unscheduled treatment follow-up, or staff handoff.
- Template metadata does not yet drive guided setup, required merge fields, checklist checks, or analytics outcome definitions.

## Existing Components To Reuse

- Existing `CampaignTemplate` registry.
- Workflow definition schema and validation.
- Campaign template UI.
- Rich merge-field catalog once implemented.
- Launch checklist.
- Response handling and analytics outcome mapping.

## New Components Required

### Template Metadata

Extend template definitions with:

- category: appointment_ops, recall, treatment, callback, reactivation
- goal/outcome labels
- supported channels
- required readiness checks
- required merge fields
- default compliance content class
- default audience/eligibility rules
- default frequency cap
- default staff handoff reason
- analytics outcome map
- sample preview context

### Priority Templates

- Appointment confirmation with YES response handling.
- Appointment reminder with date/time/provider/location fields.
- No-show recovery.
- Cancellation rebooking.
- Callback automation.
- Overdue hygiene recall.
- Reactivation/lapsed patient.
- Unscheduled treatment follow-up.

### Guided Setup

Each template should expose a small configuration form before opening the advanced builder:

- goal
- location
- audience source
- channel sequence
- send timing
- message copy variant
- staff handoff behavior
- launch checklist preview

## End-To-End Implementation Approach

1. Define template metadata schema compatible with current checked-in registry.
2. Add canonical outcome labels for each template.
3. Update existing four templates to include metadata, compliance, required fields, and sample contexts.
4. Add four new P0/P1 templates:
   - no-show recovery
   - cancellation rebooking
   - callback automation
   - unscheduled treatment follow-up
5. Update template clone API to copy metadata into workflow settings where needed.
6. Add guided setup page for template cloning.
7. Add frontend template cards grouped by dental category.
8. Connect templates to launch checklist and merge-field validation.
9. Add tests proving all template definitions validate and only use cataloged merge fields.
10. Add staging sample data for preview/dry-run.

## Timeline

Estimated duration: 2 weeks.

- Days 1-2: template metadata schema and migration/refactor of existing templates.
- Days 3-5: new template definitions and backend validation tests.
- Days 6-8: frontend category cards and guided setup.
- Days 9-10: checklist/merge-field/analytics mapping integration and staging QA.

## Architecture Decisions

- Keep templates as normal workflow definitions plus metadata, not special runtime code.
- Prefer conservative default attempt counts and clear staff fallback.
- Keep voice templates parameterized because Retell agent IDs are clinic-specific.
- Treat template outcome labels as analytics contract inputs.

## Technical Considerations

- Voice templates cannot hardcode `retell_agent_id`; clone/setup must require a selected configured voice profile.
- Appointment and recall templates should require NexHealth readiness checks.
- Treatment follow-up depends on the data-flow plan because treatment plans are PMS-limited.
- Marketing or revenue-recovery copy needs explicit content class and consent behavior.
- Templates should use only merge fields available for their trigger type.

## Dependencies

- Rich merge fields.
- Launch checklist.
- Response handling for confirmation/reschedule/handoff outcomes.
- Basic analytics outcome mapping.
- NexHealth treatment-plan/recall data flow for treatment and recall templates.

## Edge Cases

- Clinic clones a voice template without voice provisioning.
- Template uses recall fields but clinic PMS does not support recalls.
- Treatment-plan template is selected for unsupported PMS.
- Location lacks booking link configuration.
- User edits template copy and removes required STOP/HELP compliance language.
- Template is updated after clinics have older cloned workflows.

## Risks

- Too many templates overwhelm the UI.
- Templates imply data support that is not available for a clinic's PMS.
- Copy defaults accidentally cross content-class boundaries.
- Template analytics become inconsistent if outcome labels drift.

## Validation Strategy

- Unit tests that every template validates against workflow schema.
- Unit tests that every template token exists in the merge-field catalog.
- Snapshot tests for template metadata.
- Frontend tests for category filtering and guided setup.
- Manual staging clone/publish/dry-run for each template.

## Deployment Considerations

- Launch with priority templates first and mark unsupported templates as unavailable per PMS capability.
- Keep existing four templates stable for backward compatibility.
- Add metadata fields in a backward-compatible way.
- Feature-flag guided setup until launch checklist is ready.

## Future Extensibility

- Group-level DSO templates with locked compliance copy.
- A/B copy variants.
- Template performance benchmarks.
- Template marketplace/admin curation.
