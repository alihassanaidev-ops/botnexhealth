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
  evaluated in the location timezone;
- the patient has no future appointment (any scheduled/booked/pending visit);
- the patient is not inside the campaign's re-enrollment cooldown.

A patient with several due recalls is collapsed to one **primary** recall
(earliest due, then approved-list order) so the booking type is stable.
Enrollment is idempotent per `(workflow version, patient, month)`; the scan also
honors `max_enrollments_per_run` per workflow.

The scanner reports reason counters (`recalls_skipped_recall_type`,
`recalls_skipped_due_window`, and the GoTracker/treatment counters) for dry-run
review.

## Where the settings live

All recall-specific settings are on the workflow definition, so a campaign is
self-contained and location-scoped like every other campaign:

- **`PmsRecallSource`** (the schedule trigger's source) carries
  `approved_recall_type_ids`, `due_within_days`, and `include_overdue`. These
  are editable in the builder's schedule trigger panel.
- **`SendVoiceNode`** carries `allowed_appointment_type_ids` and
  `allowed_provider_ids` — the only things the live agent may book. Editable in
  the builder's call-step panel.

Defaults are conservative: a freshly instantiated recall campaign has an empty
approved-type list (every recall type) and empty allowed booking types, which
**fails closed** at booking until configured.

## Booking constraints (voice)

The AI decides what to book during the call, so the prompt cannot be the guard.
The outbound call is correlated back to its campaign run by Retell call id
(`WorkflowVoiceAttempt.retell_call_id`), and
`src/app/services/automation/recall_booking_guard.py` resolves the run's
immutable workflow version. `list_appointment_types`, `find_appointment_slots`,
and `book_appointment` then enforce, only when the run's schedule source is a
PMS recall:

- only the voice step's `allowed_appointment_type_ids` may be offered or
  booked — an empty list refuses every booking;
- only `allowed_provider_ids` may be booked (empty = any provider);
- the earliest bookable date is `max(today, recall due date)`.

Calls whose workflow is not a recall campaign are left completely untouched, so
inbound and other outbound campaigns are unaffected.

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
4. Open each call step and set the **allowed appointment types** and
   **allowed providers**.
5. Keep the campaign **paused** and review the dry-run counters before launch.
6. Launch a small pilot, verify calls/bookings/non-duplication, then scale.
7. Test the institution emergency halt before going live.

## Known gaps

- "HYG" is an Open Dental descriptor, not part of the NexHealth appointment-type
  name; the allowed types must be picked from the synced catalog by name/id.
- GoTracker recall still requires completed appointment-history sync before it
  will scan.
