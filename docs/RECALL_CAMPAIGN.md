# Recall Campaign

The recall campaign calls patients whose cleaning recall is coming due and books
the visit on the call (voice), or reaches them by SMS with a booking link. It is
built from the existing outbound automation engine; this document is the
operator guide and the map of what is recall-specific.

## Audience

The scanner (`scan_recall_workflows`, run through the DB schedule tick) reads the
location's whole PMS recall queue and keeps a patient when **all** of these hold:

- the recall type is on the location's approved list (`recall_config`);
- the due date is inside the location's window (default: overdue plus the next
  30 days, evaluated in the location timezone);
- the patient has no future appointment (any scheduled/booked/pending visit);
- the patient is not inside the re-enrollment cooldown.

A patient with several due recalls is collapsed to one **primary** recall
(earliest due, then allowlist order) so the booking type mapping is stable.
Enrollment is idempotent per `(workflow version, patient, month)`; the scan also
honors `max_enrollments_per_run` per workflow so a large backlog drains in paced
batches instead of one unbounded fan-out.

The scanner reports reason counters (`recalls_skipped_recall_type`,
`recalls_skipped_due_window`, and the existing GoTracker/treatment counters) for
dry-run review.

## Per-location configuration

`InstitutionLocation.recall_config` (JSONB) holds:

```json
{
  "approved_recall_type_ids": ["2288000", "2288006"],
  "due_within_days": 30,
  "include_overdue": true,
  "appointment_type_map": {"2288000": "nh-1230522", "2288006": "nh-1230521"},
  "provider_ids": ["nh-502294485", "nh-506291620"]
}
```

`NULL` means "not configured": the scanner keeps its legacy behavior and no
booking guard is applied. Parse/validate with
`src/app/services/automation/recall_config.py`.

Two ways to set it:

- API: `PUT /institution/portal/locations/{loc_slug}/recall/config`
  (GET/DELETE mirror it), institution- or location-admin, location-scoped.
- CLI: `python -m src.app.scripts.configure_recall_campaign ...` (see
  `--help`). Idempotent; can also upsert the location's "Recall" voice profile.

## Booking constraints (voice)

The AI decides what to book during the call, so the prompt cannot be the guard.
The outbound call is correlated back to its campaign run by Retell call id
(`WorkflowVoiceAttempt.retell_call_id`), and
`src/app/services/automation/recall_booking_guard.py` resolves the run's
location config. `list_appointment_types`, `find_appointment_slots`, and
`book_appointment` then enforce:

- only the appointment type mapped for the run's recall type may be offered or
  booked — an unconfigured mapping **fails closed**;
- only `provider_ids` may be booked;
- the earliest bookable date is `max(today, recall due date)`.

Calls with no recall voice attempt are untouched — inbound and non-recall calls
are unaffected.

## Voice template

`recall-voice-6month` (`src/app/services/automation/campaign_templates.py`):
`schedule` trigger sourced from `pms_recall`, three bounded call attempts, and a
`switch` on `call_outcome` routing to `booked`, `callback_requested`,
`do_not_contact`, `unreachable`, or a staff handoff default. It requires an
outbound voice profile at instantiation.

The publishing path stores the campaign's `content_class` on the workflow
version; the runtime gate reads it from there (`step_dispatcher`), so a `recall`
campaign enforces its consent class rather than silently running under implied
consent.

## Compliance

- Content class `recall` requires an express (written) consent basis
  (`compliance_gate_service`). A location with no recorded voice consent will
  have calls blocked — decide the basis with the clinic before launch.
- Quiet hours come from the location's operating hours; the client's requested
  window is 9am–8pm, Mon–Fri, `America/Toronto`.
- Spoken opt-out suppression relies on `settings.retell_optout_analysis_key`;
  it is inert until that key is set.

## Setup checklist (staging or production)

1. Ensure the location is bound to its PMS (subdomain + location id) and has
   appointment types and providers synced.
2. Create the "Recall" outbound voice profile (Retell agent id + from number).
3. Set `recall_config` (API or CLI).
4. Confirm quiet hours and the consent basis.
5. Instantiate `recall-voice-6month`, then keep the workflow **paused** until
   the dry run is reviewed.
6. Run a dry run: invoke the recall scan (or wait for the schedule tick) and
   review the reason counters; confirm only approved types/patients appear.
7. Launch a small pilot, verify calls/bookings/non-duplication, then scale.
8. Test the institution emergency halt before going live.

## Known gaps

- The client's "HYG" is an Open Dental descriptor, not part of the NexHealth
  appointment-type name; the mapping must use explicit cached type ids.
- No dashboard editor for `recall_config` yet (API + CLI only).
- GoTracker recall still requires completed appointment-history sync before it
  will scan.
