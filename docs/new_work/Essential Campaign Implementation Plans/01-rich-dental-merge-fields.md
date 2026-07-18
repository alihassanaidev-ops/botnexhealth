# Essential 1 - Rich Dental Merge Fields Implementation Plan

## What Needs To Be Built

Expand the campaign merge-field system from four static fields into a dental-aware catalog that can safely render appointment, provider, location, recall, booking, guardian, language, and callback context in SMS, email, voice prompt metadata, previews, validation, and dry-runs.

The goal is not just more placeholders. The builder must show only fields that are reliable for the selected trigger and channel, explain missing context before launch, and prevent raw `{{token}}` text from reaching patients.

## Existing System Context

The backend already has:

- `STATIC_MERGE_FIELDS` in `src/app/services/automation/template_renderer.py`.
- `render_sms_body(template, contact, location, context)` that resolves static fields and then trigger context.
- `GET /automation/workflows/merge-fields`, sourced from the renderer.
- Workflow dry-run and validation routes.
- Appointment trigger metadata from NexHealth webhooks/backfill.
- Location reference data for providers, appointment types, operatories, and NexHealth binding.

The frontend already has:

- `nexus-dashboard-web/src/lib/workflow/merge-fields.ts`.
- Message preview and token insertion patterns.
- Builder validation for unknown tokens.

Current gap:

- Only `patient_first_name`, `patient_last_name`, `patient_full_name`, and `clinic_name` are cataloged.
- Appointment, provider, booking, recall, callback, preferred-language, guardian, and location contact fields are not first-class catalog fields.
- The UI does not filter merge fields by trigger type or tell the user which fields are unavailable.

## Existing Components To Reuse

- Backend renderer/catalog single-source-of-truth pattern.
- Workflow definition validation service.
- Dry-run simulation endpoint.
- Existing NexHealth appointment projection and trigger metadata.
- Existing provider, appointment type, operatory, and location sync tables.
- Existing frontend merge-field cache and message preview.

## New Components Required

### Backend

- `MergeFieldCatalogService`
  - returns fields by trigger type, channel, and availability level
  - exposes static fields plus context-backed fields
  - marks each field as `required_context`, `optional_context`, or `derived`

- `MergeContextBuilder`
  - builds a normalized context object for appointment, recall, manual, bulk, and callback triggers
  - resolves reference names from IDs where local reference data exists
  - falls back to empty string only at final render time

- Expanded merge-field API:
  - `GET /automation/workflows/merge-fields?trigger_type=appointment_offset&channel=sms`
  - response fields: `name`, `token`, `label`, `description`, `sample`, `group`, `availability`, `requires`, `phi_level`, `channels`

- Validation rules:
  - warn when template uses fields unavailable for the workflow trigger
  - block publish when required merge context cannot be produced for a required message
  - warn for PHI-heavy fields in SMS/voice unless explicitly allowed

### Field Groups

- Patient:
  - `patient_first_name`, `patient_last_name`, `patient_full_name`
  - `patient_preferred_language`
  - `guardian_first_name`, `guardian_full_name` where available

- Appointment:
  - `appointment_date`, `appointment_time`, `appointment_datetime`
  - `appointment_type`, `appointment_status`
  - `provider_name`, `operatory_name`

- Location:
  - `clinic_name`, `location_name`, `location_phone`, `location_address`

- Booking/action:
  - `booking_link`, `confirmation_link`, `reschedule_link`
  - link values must be generated per campaign/run when possible, not stored in reusable templates

- Recall:
  - `recall_due_date`, `recall_type`, `last_visit_date` when available

- Callback:
  - `callback_requested_at`, `callback_reason`, `preferred_callback_time`

## End-To-End Implementation Approach

1. Add a typed merge-field catalog model that supports field source, trigger scope, channel scope, PHI level, and sample value.
2. Move `STATIC_MERGE_FIELDS` into the new catalog model while preserving current tokens.
3. Add context-backed field definitions for appointment, recall, location, booking, and callback groups.
4. Implement `MergeContextBuilder` for each trigger type using existing run context plus local reference data.
5. Update SMS, email, and voice executors to call the same renderer with normalized context.
6. Extend backend validation to flag unavailable tokens by trigger type.
7. Extend dry-run sample contexts so previews show realistic dental values.
8. Update frontend merge-field picker to group fields and filter by selected trigger/channel.
9. Update frontend preview/unknown-token logic to use backend catalog metadata.
10. Add release migration/fixtures for sample templates that include appointment date/time and booking links.

## Timeline

Estimated duration: 2 weeks.

- Days 1-2: catalog schema, API response contract, and renderer refactor.
- Days 3-5: appointment/location/provider/booking context builder and backend tests.
- Days 6-7: recall/callback context builder and validation rules.
- Days 8-9: frontend grouped picker, trigger/channel filtering, and previews.
- Day 10: staging validation with appointment reminder, confirmation, recall, and callback templates.

## Architecture Decisions

- Keep the renderer permissive at final substitution time, but make publish validation strict enough to catch unavailable tokens.
- Keep one catalog source on the backend; frontend fallback remains only for offline/initial render.
- Do not store generated booking/confirmation/reschedule links as static contact fields. Generate them per run so links can expire and be attributed.
- Avoid clinical/procedure details in SMS by default; include appointment type/procedure only when the campaign content class allows it.

## Technical Considerations

- Merge field availability is conditional: appointment fields are safe for appointment-offset triggers, but not for manual campaigns unless the enrollment payload supplies appointment context.
- NexHealth appointment webhooks include provider IDs/names, appointment type IDs, operatory IDs, patient IDs, status fields, and timestamps, but not every PMS will populate every field consistently.
- Preferred language and guardian fields may be PMS-dependent. They should be optional fields with clear empty-state behavior.
- Email can support richer formatting, but all channels should use the same canonical token names.
- Token names should stay snake_case for compatibility with the existing regex.

## Dependencies

- NexHealth appointment projection/backfill for appointment fields.
- Reference-data sync for provider, appointment type, operatory, and location names.
- Booking/reschedule link generation or existing patient booking route integration.
- Template expansion plan for dental-specific templates.
- Launch checklist plan for merge-field readiness.

## Edge Cases

- Appointment has no provider name.
- Appointment has a provider ID but local provider sync is stale.
- Patient has multiple guardians or no guardian.
- Recall row has a due date but no last visit date.
- Manual enrollment uses appointment fields without appointment context.
- Reschedule changes appointment date after the run has already rendered a message preview.
- Link generation fails during send.

## Risks

- Overexposing PHI in SMS copy.
- Builder advertises a field that the engine cannot resolve for a trigger.
- Different channels render different values for the same token.
- Link tokens create attribution or security gaps if reused across patients.

## Validation Strategy

- Unit tests proving every catalog token can be rendered by the backend renderer.
- Unit tests for trigger-specific available/unavailable token validation.
- Integration tests for appointment trigger context rendering from projected NexHealth appointment rows.
- Frontend tests for grouped picker, trigger filtering, unknown token warnings, and preview sample values.
- Manual staging test: appointment confirmation template with date, time, provider, location phone, and confirmation link.

## Deployment Considerations

- Ship backend catalog expansion first while preserving current API shape where possible.
- Add frontend grouped picker behind the campaign-builder feature flag.
- Treat PHI-heavy fields as disabled or warning-only until compliance copy review is complete.
- Log unresolved required context as structured non-PHI metrics.

## Future Extensibility

- Locale-aware date/time formatting by patient preferred language.
- Per-location custom merge fields.
- Treatment-plan fields after the NexHealth treatment-plan data flow is implemented.
- Preview using real masked sample patients from an audience preview.
