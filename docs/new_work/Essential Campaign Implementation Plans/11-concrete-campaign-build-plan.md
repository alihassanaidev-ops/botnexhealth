# Concrete Campaign Build Plan For CTO Review

Date: 2026-07-18

This is the implementation-facing build plan distilled from the detailed essential campaign plans. It defines what we will build, how NexHealth data enters the system, where data is stored, how campaign enrollment decisions are made, and what is capability-gated by PMS support.

## Final Build Scope

Build the multi-channel campaign foundation:

- NexHealth v3/v20240412 migration for the campaign data paths
- rich appointment/patient/callback merge fields
- launch checklist
- campaign overview and run progress
- callback trigger and voice outcome UI exposure
- deterministic patient response handling
- dental templates for appointment reminder, confirmation, no-show/cancellation recovery, callback automation, recall, and treatment-plan follow-up
- basic outcome analytics for sends, calls, responses, confirmations, failures, handoffs, and cost

Capability-gate these by PMS support:

- procedure/revenue analytics
- insurance-based segmentation/campaigns

## Why This Scope Is Safe

NexHealth documents appointment and patient webhook/API data sufficient for appointment/callback campaigns:

- patient/contact identity
- phone/email/name
- appointment ID
- appointment start/end time
- provider/location context
- appointment type/operatory IDs
- confirmation status
- missed/no-show status
- cancelled/deleted status
- check-in/check-out status
- sync timestamps

This is enough to create a local campaign decision context for appointment/callback campaigns. Recall and treatment-plan campaigns will use their own working sets and will be enabled only when the clinic's PMS supports the required NexHealth resource. Final send/call eligibility still uses our own consent, opt-out, suppression, quiet-hours, frequency-cap, and workflow-state checks.

## End-To-End Data Flow

1. NexHealth sends an appointment or patient webhook, or our scheduled job pulls recall data.
2. Backend verifies webhook signature.
3. Backend deduplicates the event using a stable event key.
4. Backend resolves institution and location from NexHealth subdomain/location ID.
5. Backend stores minimum data in local working-set tables.
6. Campaign trigger service checks active workflows that match the event or polled row.
7. Contact is resolved by `nexhealth_patient_id`; phone/email match is used only when unambiguous.
8. Eligibility checks run:
   - correct institution/location
   - workflow active and published
   - no duplicate active run
   - consent exists
   - not opted out
   - not suppressed/DNC
   - frequency cap passes
   - quiet hours/send window can be respected
   - appointment/recall state is still valid
9. If eligible, backend creates an `automation_workflow_run`.
10. Scheduler executes workflow nodes.
11. Before SMS/call/email, backend revalidates PMS state if needed.
12. Backend builds a clean context object and passes it to SMS/email/voice/AI executor.
13. Attempts, responses, outcomes, costs, and handoffs are stored and surfaced in campaign UI.

## Webhooks We Subscribe To

First scope:

- `appointment_created`
- `appointment_updated`
- `appointment_insertion`
- `patient_created`
- `patient_updated`
- `sync_status_read_change`
- `sync_status_write_change`

Later PMS-gated scope:

- `procedure_created`
- `procedure_updated`
- `treatment_plan_created`
- `treatment_plan_updated`
- `treatment_plan_deleted`
- patient insurance coverage events if insurance campaigns are approved later

## Scheduled GET APIs

Use scheduled REST pulls for data that cannot rely on webhooks:

- `GET /appointments` for appointment backfill/reconciliation
- `GET /patients` for patient/contact backfill/reconciliation
- `GET /patient_recalls` for recall eligibility
- `GET /sync_status` because sync-status webhooks do not reliably report green-to-red failures
- `GET /procedures` for PMS-gated procedure attribution
- `GET /treatment_plans` for PMS-gated treatment follow-up

Important recall decision:

- NexHealth does not document recall-specific webhooks such as `patient_recall_created`, `patient_recall_updated`, or `patient_recall_due`.
- Recall campaigns require scheduled `GET /patient_recalls` polling and a local `recall_working_set`.

## Existing Tables We Can Use

Already present:

- `contacts`
- `nexhealth_webhook_subscriptions`
- `nexhealth_webhook_events`
- `appointment_working_set`
- `automation_workflows`
- `automation_workflow_versions`
- `automation_workflow_runs`
- `automation_workflow_step_executions`
- `automation_workflow_timers`
- `automation_workflow_events`
- `consent_records`
- `sms_suppressions`
- `do_not_contact`
- `sms_history_logs`
- `inbound_sms_messages`
- `outbound_voice_profiles`
- `workflow_voice_attempts`
- `usage_events`
- `usage_cost_rollups`
- `location_operating_hours`

These cover current workflow state, appointment projection, channel attempts, voice config, SMS/voice consent, suppression, DNC, quiet-hours source, and usage/cost attribution.

## Schema We Need To Add

Required for full campaign data brain:

- `patient_working_set`
  - minimal NexHealth patient projection for campaign matching/context

- `recall_working_set`
  - polled recall rows from `GET /patient_recalls`

- `nexhealth_resource_events`
  - generalized event ledger for patient/procedure/treatment/sync webhooks beyond appointments

- `pms_sync_watermarks`
  - per location/resource backfill and polling watermarks

- `campaign_response_events`
  - normalized SMS/voice/email/booking-link responses

- `campaign_staff_handoffs`
  - human follow-up tasks from ambiguous replies, failed automation, or clinical/billing questions

- `campaign_audience_definitions`
  - persisted segment/filter rules

- `campaign_audience_previews`
  - preview counts and exclusion reason summaries

Useful for scale:

- `outbound_contact_attempts` or `contact_frequency_counters`
  - fast frequency-cap checks across SMS, voice, and email

PMS-gated working sets:

- `treatment_plan_working_set`
- `procedure_working_set` for procedure/revenue analytics when that capability is enabled

## Campaign Enrollment Logic

A contact is enrolled only when all of these pass:

1. Trigger candidate exists:
   - appointment event/backfill
   - callback request
   - recall due row
   - manual/CSV/audience selection
   - treatment-plan state

2. Contact resolution succeeds:
   - prefer `nexhealth_patient_id`
   - fallback to phone/email only if one clear match exists
   - ambiguous matches become staff review, not auto-enrollment

3. Campaign eligibility passes:
   - workflow active
   - published version exists
   - location matches
   - patient has valid appointment/recall/treatment state
   - no duplicate active run for same trigger reference

4. Safety gates pass:
   - consent
   - opt-out/suppression/DNC
   - frequency cap
   - quiet hours/send window
   - channel readiness

## SMS/Call/Email Decision Logic

The workflow template defines the intended channel sequence. Runtime does not invent a channel sequence.

For each step:

1. Read current workflow node: SMS, voice, email, wait, condition, or exit.
2. Check channel readiness.
3. Check patient reachability.
4. Check consent and opt-outs for that channel.
5. Check quiet hours and frequency cap.
6. Revalidate PMS state if the run is appointment/recall/treatment-bound.
7. Execute the step or follow an explicit fallback branch.
8. If no safe action exists, mark skipped/suppressed and create handoff when needed.

## Data Passed To Outbound AI Brain

The AI/voice brain receives a prepared context object, not raw webhook payloads.

Example context:

```json
{
  "patient": {
    "first_name": "Sarah",
    "phone": "+1...",
    "preferred_language": "en"
  },
  "campaign": {
    "type": "appointment_confirmation",
    "workflow_run_id": "...",
    "current_step": "voice-call"
  },
  "appointment": {
    "id": "1822",
    "start_time": "2026-07-22T14:00:00Z",
    "provider_name": "Dr. Smith",
    "confirmed": false,
    "cancelled": false,
    "patient_missed": false
  },
  "eligibility": {
    "consent_ok": true,
    "not_suppressed": true,
    "within_frequency_cap": true,
    "within_send_window": true
  }
}
```

## Build Sequence

1. Migrate NexHealth campaign data paths to v3/v20240412.
2. Replace offset pagination with cursor pagination for campaign-used list endpoints.
3. Update renamed endpoints used by campaign data paths:
   - `/appointment_slots` -> `/available_slots`
   - `/availabilities` -> `/working_hours`
   - `/recalls` or legacy recall paths -> `/patient_recalls`
4. Recreate webhook subscriptions under v3 so incoming payloads are v3-shaped.
5. Add/adjust working-set schema and watermarks.
6. Extend webhook ingestion beyond appointment-only where needed.
7. Add patient working-set backfill and webhook processing.
8. Add recall working-set polling.
9. Add treatment-plan working set and webhook/backfill processing where supported.
10. Add merge context builder and rich merge-field catalog.
11. Add launch checklist.
12. Add campaign enrollment/audience preview rules.
13. Add response events and staff handoffs.
14. Expose callback trigger and voice outcome UI.
15. Add campaign overview/run progress.
16. Add basic analytics rollups.
17. Add dental templates and guided setup.

## Finalized Decisions For Development

Implementation decisions:

- Migrate NexHealth campaign data paths to v3/v20240412 during this implementation.
- Include appointment, callback, recall, and treatment-plan campaign workflows.
- Include voice, SMS, and email.
- Use our local consent/suppression/DNC as source of truth.
- Treat NexHealth `unsubscribe_sms` as an additional blocking hint.
- Use max 1 outbound campaign contact per patient per day and max 3 per rolling 7 days.
- Use 9 AM-6 PM local quiet-hours defaults unless clinic hours are stricter.
- Allow AI voice booking/rescheduling through the existing NexHealth booking flow.
- Capability-gate PMS-specific features.

## Not In First Build

- universal recall automation across PMSs that do not support `patient_recalls`
- treatment-plan follow-up for PMSs that do not support treatment plans
- procedure/revenue analytics
- insurance-based campaigns
- arbitrary advanced segmentation
- full PMS database replica
- raw webhook payloads passed directly to the AI brain
