# Recall Campaign

The recall campaign calls patients whose cleaning recall is coming due and books
the visit on the call (voice), or reaches them by SMS with a booking link. It is
built from the existing outbound automation engine and is configured entirely on
the campaign — there is no separate location-level recall settings screen.

## Audience

The scanner (`scan_recall_workflows`, run through the DB schedule tick) reads the
location's whole PMS recall queue. For each recall campaign at that location it
keeps a patient when **all** of these hold:

- the recall type is among the campaign's **approved recall type ids** (empty =
  every recall type);
- the due date is inside the campaign's window
  (`today + due_within_days`, and not in the past unless `include_overdue`),
  evaluated in the location timezone (a recall with no due date is skipped);
- the patient has no future appointment (any scheduled/booked/pending visit);
- the patient is not inside the campaign's re-enrollment cooldown.

A patient with several due recalls is collapsed to one **primary** recall
(earliest due, then approved-list order) so the booking type is stable.
Enrollment is idempotent per `(workflow version, patient, month)`; the scan also
honors `max_enrollments_per_run` per workflow.

### Live patient refresh

For NexHealth, each patient about to be enrolled is read live
(`GET /patients/{id}` with `last_visited_appointment`) and their contact is
refreshed from that read before the run is queued. Recall therefore does not
depend on the full NexHealth patient sync or webhooks being on: only patients
actually enrolled are fetched and stored. A patient the PMS marks inactive is
never called. If the live read fails the scanner falls back to an existing
contact, and skips the patient when there is none.

The scanner reports reason counters (`recalls_skipped_recall_type`,
`recalls_skipped_due_window`, `patients_skipped_live_fetch_failed`,
`patients_skipped_inactive`, and the GoTracker/treatment counters) for dry-run
review.

## Where the settings live

All recall-specific settings are on the workflow definition, so a campaign is
self-contained and location-scoped like every other campaign:

- **`PmsRecallSource`** (the schedule trigger's source) carries
  `approved_recall_type_ids`, `due_within_days`, and `include_overdue`. These
  are editable in the builder's schedule trigger panel, as is
  `allowed_appointment_type_ids` (see Booking).

A freshly instantiated recall campaign has an empty approved-type list, which
means every recall type; set it before launch.

## Booking (voice)

The recall source carries `allowed_appointment_type_ids` (builder: schedule
trigger panel, "Bookable appointment types"). When it is set, a call placed by
that recall campaign sees and books only those types:

- `list_appointment_types` returns only the allowed types;
- `list_providers` shows only the allowed types per provider and drops
  providers who offer none of them;
- `find_appointment_slots` and `book_appointment` refuse any other type.

The call is matched to its campaign by Retell call id
(`WorkflowVoiceAttempt.retell_call_id`) in
`src/app/services/automation/recall_booking_guard.py`. Inbound calls, other
campaigns, and a recall campaign with an empty list are unrestricted. Which
allowed type to book for a given patient (age, recall type) and which provider
and date are decided in the Retell agent prompt. Type ids differ per PMS, so
set the list per campaign (Kadri production: Adult recall (16+), Teen Recall
(13-15), Kids recall (6-12), Kids recall (3-5), 2 units scale, 3 units scale).

## What the agent receives

A recall call sends the agent only the minimum patient data:
`patient_first_name` (also `first_name`), `user_number` (the number dialled),
`recall_type_name`, and `recall_due_date`, plus the non-patient
`clinic_name`, `compliance_disclosure`, and `leave_voicemail`. No last name,
email, date of birth, or visit history is sent. The agent verifies the patient
with `lookup_patient` during the call, which supplies the patient ID needed to
book and, on a full-detail lookup, `age` (whole years on the clinic's local
date, `null` when the DOB is missing) used to choose the hygiene appointment
type. The server computes the age so the agent never does date arithmetic; it
is an added field available to every agent that calls `lookup_patient`. Other
campaigns keep their full merge context.

## Voice template

`recall-voice-6month` (`src/app/services/automation/campaign_templates.py`):
`schedule` trigger sourced from `pms_recall`, three bounded call attempts, and a
`switch` on `call_outcome` routing to `booked`, `callback_requested`,
`do_not_contact`, `unreachable`, or a staff handoff default. It requires an
outbound voice profile at instantiation.

Publishing stores the campaign's `content_class` on the workflow version; the
runtime gate reads it from there (`step_dispatcher`), so a `recall` campaign
enforces its consent class rather than silently running under implied consent.

## Compliance

- Content class `recall` requires an express (written) consent basis
  (`compliance_gate_service`). A location with no recorded voice consent will
  have calls blocked — decide the basis with the clinic before launch.
- Quiet hours come from the location's operating hours.
- Spoken opt-out suppression relies on `settings.retell_optout_analysis_key`;
  it is inert until that key is set.

## Setup (staging or production)

1. Ensure the location is bound to its PMS (subdomain + location id) and has
   appointment types, providers, and an outbound voice profile.
2. In the dashboard, instantiate the **Recall Outreach — Voice** template and
   pick the voice profile.
3. In the builder, open the schedule trigger and set the **approved recall
   type ids**, **due window**, and **include overdue**.
4. Make sure the Retell recall agent's prompt maps recall type and age to
   the clinic's hygiene appointment types.
5. Keep the campaign **paused** and review the dry-run counters before launch.
6. Launch a small pilot, verify calls/bookings/non-duplication, then scale.
7. Test the institution emergency halt before going live.

## Known gaps

- "HYG" is an Open Dental descriptor, not part of the NexHealth appointment-type
  name; the agent prompt must refer to types by their NexHealth names.
- GoTracker recall still requires completed appointment-history sync before it
  will scan.
