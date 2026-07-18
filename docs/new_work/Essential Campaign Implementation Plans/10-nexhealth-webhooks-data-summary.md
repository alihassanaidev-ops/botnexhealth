# NexHealth Webhooks And Data Summary

Date: 2026-07-18

This is the concise implementation summary for the NexHealth webhook data documented in NexHealth's official webhook/API docs for campaign workflows. The field lists below are not guessed; they are taken from NexHealth's documented webhook examples and API response documentation. Any PMS/API-version variability is called out explicitly.

Sources:

- NexHealth webhook subscriptions: https://docs.nexhealth.com/reference/webhook-subscriptions
- NexHealth patients API: https://docs.nexhealth.com/reference/getpatients
- NexHealth appointments API: https://docs.nexhealth.com/reference/getappointments
- NexHealth patient recalls API: https://docs.nexhealth.com/reference/getpatientrecalls

## Webhooks We Subscribe To First

### Appointment Webhooks

Events:

- `appointment_created`
- `appointment_updated`
- `appointment_insertion`

Documented appointment webhook data:

- `id`
- `patient_id`
- `provider_id`
- `provider_name`
- `start_time`
- `end_time`
- `confirmed`
- `patient_missed`
- `cancelled`
- `deleted`
- `checkin_at`
- `checked_out`
- `checked_out_at`
- `appointment_type_id`
- `operatory_id`
- `location_id`
- `timezone`
- `foreign_id`
- `foreign_id_type`
- `last_sync_time`
- `patient_confirmed`
- `patient_confirmed_at`
- `confirmed_at`
- `cancelled_at`
- `created_at`
- `updated_at`
- `unavailable`
- `note`
- `misc.is_booked_on_nexhealth`
- `is_guardian`
- `is_new_clients_patient`
- `sooner_if_possible`
- `referrer`
- `is_past_patient`
- `timezone_offset`

How we use it:

- appointment reminders
- appointment confirmations
- reschedule/cancellation detection
- no-show or missed-patient recovery
- appointment merge fields
- live campaign progress and outcome tracking

Walk-in note:

- NexHealth does not have a separate walk-in webhook.
- If a walk-in is entered as a patient or appointment in the PMS, we receive normal `patient_created`, `patient_updated`, `appointment_created`, or `appointment_updated` events.
- If the clinic does not enter the walk-in into the PMS/NexHealth, we receive no NexHealth event.

## Patient Webhooks

Events:

- `patient_created`
- `patient_updated`

Documented patient webhook/API data:

- `id`
- `email`
- `first_name`
- `middle_name`
- `last_name`
- `name`
- `created_at`
- `updated_at`
- `foreign_id`
- `foreign_id_type`
- `bio.phone_number`
- `bio.cell_phone_number`
- `bio.home_phone_number`
- `bio.work_phone_number`
- `bio.date_of_birth`
- `bio.address_line_1`
- `bio.address_line_2`
- `bio.street_address`
- `bio.city`
- `bio.state`
- `bio.zip_code`
- `bio.gender`
- `bio.new_patient`
- `bio.non_patient`
- `inactive`
- `last_sync_time`
- `guarantor_id`
- `unsubscribe_sms`
- `billing_type`
- `chart_id`
- `preferred_language`
- `provider_id`

PMS-specific patient fields:

- NexHealth documents `billing_type`, `chart_id`, and `preferred_language` as partially supported fields, not universally available for every PMS.

How we use it:

- link NexHealth patients to local contacts
- keep campaign contact data fresh
- support patient merge fields
- determine whether a patient is active/inactive
- support future language-based segmentation

Important note:

- NexHealth patient data helps with matching and context, but our own local consent/suppression system remains the source of truth for whether we are allowed to message or call a patient.

## Sync Status Webhooks

Events:

- `sync_status_read_change`
- `sync_status_write_change`

Documented sync-status webhook data:

- `institution_id`
- `sync_source_type`
- `sync_source_name`
- `emr.id`
- `emr.name`
- `emr.display_name`
- `emr.type`
- `read_status`
- `read_status_at`
- `write_status`
- `write_status_at`
- `locations`

How we use it:

- integration health checks
- launch checklist readiness
- campaign data freshness warnings
- deciding whether PMS read/write actions are safe

Important limitation:

- NexHealth says sync status webhooks signal recovery from red to green.
- They do not reliably notify us when sync goes from green to red.
- So we still need scheduled polling of `GET /sync_status`.

## Later Webhooks For Advanced Campaigns

### Procedure Webhooks

Events:

- `procedure_created`
- `procedure_updated`

Documented procedure webhook data:

- `id`
- `location_id`
- `patient_id`
- `provider_id`
- `appointment_id`
- `code`
- `name`
- `status`
- `updated_at`
- `body_site.tooth`
- `body_site.surface`
- `fee.amount`
- `fee.currency`
- `start_date`
- `end_date`

How we use it:

- procedure-based follow-up
- completed-visit analytics
- treatment/revenue attribution

Important note:

- Procedure webhook support is PMS-specific and the data is clinically sensitive.
- It must be capability-gated and not exposed in patient-facing messages by default.

### Treatment Plan Webhooks

Events:

- `treatment_plan_created`
- `treatment_plan_updated`
- `treatment_plan_deleted`

Documented treatment-plan webhook data:

- `id`
- `name`
- `patient_id`
- `updated_at`
- `status`
- `procedures[]`
- related procedure fields such as `id`, `location_id`, `patient_id`, `provider_id`, `appointment_id`, `code`, `name`, `status`, `updated_at`, `body_site`, `fee`, `start_date`, and `end_date`

How we use it:

- unscheduled treatment follow-up
- accepted-but-not-booked workflows
- treatment plan analytics

Important note:

- Treatment plans are not supported for every PMS.
- NexHealth currently documents support mainly for Dentrix, Dentrix Enterprise, Eaglesoft, and Open Dental.

## Patient Recalls

Patient recalls are patients who are due or overdue for routine follow-up care, usually dental hygiene cleanings/checkups.

Example:

- patient is due for a 6-month cleaning
- patient is overdue for recall
- patient has no future appointment

Important missing webhook:

- NexHealth's documented webhook subscription list does not include recall-specific webhooks such as `patient_recall_created`, `patient_recall_updated`, or `patient_recall_due`.

What this means:

- Recall campaigns cannot be driven by webhook events alone.
- We need a scheduled backend job that calls `GET /patient_recalls`.
- That job pulls recall data, updates a local recall working set, and enrolls eligible patients.

Recall eligibility checks:

- patient is due or overdue for recall
- patient has no future appointment
- patient belongs to the campaign location
- patient has required consent
- patient is not opted out or suppressed
- patient is not already active in the same campaign

## Final Implementation Rule

Use webhooks for future changes, but use REST backfills for historical and audience data.

In simple terms:

- appointments and patients: webhook-led plus REST backfill
- sync health: webhook plus scheduled polling
- recalls: scheduled REST polling, no recall webhook
- procedures and treatment plans: webhook plus REST backfill, but PMS-gated
