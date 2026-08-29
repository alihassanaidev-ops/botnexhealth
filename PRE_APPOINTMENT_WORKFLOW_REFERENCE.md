# Pre-Appointment Confirmation: Current Flow in Plain English

Last reviewed against the current workflow: August 13, 2026

## Purpose

This workflow calls a patient before a selected surgery or major appointment. It
can record a confirmation, cancellation, reschedule request, callback request, or
request not to be called again.

GoTracker remains the main appointment record. Our system schedules and tracks the
calls, and the automated calling service reports what happened during each call.

This document describes what the workflow does today. It also calls out places
where a business decision or future improvement is still needed.

## Settings chosen before the workflow is turned on

Each clinic chooses:

1. Which appointment reasons are eligible for a call.
2. How many hours before the appointment the first call should be made. The
   default is 24 hours.
3. How long to wait after the first unsuccessful call before trying again. The
   default is 5 hours.
4. How long to wait after the second unsuccessful call before the final attempt.
   The default is 5 hours.
5. The patient voice cooldown. The default is 24 hours.
6. Which automated calling profile will speak with the patient.

The clinic's quiet hours, time zone, consent rules, and do-not-contact rules also
apply to every call.

## How an appointment enters the workflow

1. GoTracker reports that an appointment was created or changed.
2. A duplicate report is ignored, so it does not create a duplicate workflow.
3. A new appointment is scheduled for its first call at the clinic's selected
   number of hours before the appointment.
4. If the intended first-call time has already passed when the appointment is
   received, no pre-appointment workflow is started for that appointment.
5. If clinic staff move the appointment to a new time, waiting work for the old
   time is cancelled and a new workflow can be scheduled for the new time.
6. Changes that came from our own workflow are recognized so they do not create an
   endless update loop.

A cancellation, No Show, Office Cancel, or Short Cancel stops active work for the
appointment. These are all treated as final non-attending results and are not
eligible for a pre-appointment call.

If a reschedule happens while a call is already in progress, the call is allowed
to finish. Waiting calls for the old time are cancelled, and the appointment is
checked again before any later call.

## Eligibility for the first call

When the workflow starts, both of these conditions must be true:

- The appointment is still Booked.
- The first appointment reason matches one of the reasons selected by the clinic.

Capitalization and extra spaces do not matter, but the wording otherwise needs to
match. Only the first appointment reason is used. If an appointment has more than
one reason, the later reasons do not affect eligibility.

The reason is checked from the appointment information received when the workflow
was scheduled. It is not read again from GoTracker immediately before the call.

## Checks made before every call attempt

Before dialing, the system checks again that:

- the appointment has not been cancelled, marked as a non-attending status, or
  moved to a different time;
- the clinic has not used the emergency stop;
- the current time is inside the clinic's allowed calling hours;
- the patient is not on a do-not-contact list;
- the patient has a usable phone number and is allowed to receive this type of
  care-related call; and
- another automated workflow has not attempted a voice call to the same patient
  inside the patient voice cooldown.

If it is quiet hours, the call waits until the next allowed calling time and all
checks are repeated. A cancellation, reschedule, do-not-contact rule, missing
phone number, or consent block prevents the call.

If the current GoTracker appointment cannot be checked because GoTracker is
temporarily unavailable, the workflow currently allows the call to continue. This
is a deliberate availability choice, but the clinic should approve it.

## What patient voice cooldown means

The patient voice cooldown is the minimum gap between automated voice call
attempts made to the same patient by different workflow runs. A recent attempt
counts even if the patient did not answer.

For example, with a 24-hour cooldown, a pre-appointment call will not be made if a
different automated workflow attempted to call that patient within the previous
24 hours.
The pre-appointment workflow does not wait for that cooldown to end. It skips the
attempt, records that follow-up is needed, and finishes.

The cooldown does not block the second and third attempts belonging to the same
pre-appointment workflow. Those attempts follow the two retry delays selected by
the clinic. Setting the cooldown to zero turns this cross-workflow check off.

## What happens during a call

The automated calling service places the call and reports the patient or dialing
result. The workflow waits for the analyzed result. If no result is received
within 30 minutes, the attempt is treated as a timeout. A background recovery
check also looks for results that may have arrived without the normal notification.

There are no more than three patient call attempts in one pre-appointment
workflow.

## What each call result does

| Call result | GoTracker change | What happens next |
| --- | --- | --- |
| Patient confirms | The Confirmed checkbox is set to Yes. | The workflow finishes as confirmed. The appointment status and time are not changed. |
| Patient cancels | The appointment status is changed to Cancelled. | The workflow finishes as cancelled. No date, time, provider, room, duration, reason, or confirmation change is sent by this workflow. |
| Patient asks to reschedule and provides a valid new time | Only the appointment's start date and start time are changed. | The same appointment is kept and the workflow finishes as rescheduled. After GoTracker confirms the change, a new pre-appointment workflow can be scheduled for the new time if it is still early enough. |
| Patient asks to reschedule but no usable new time is provided | Nothing is changed in GoTracker. | A local “reschedule time missing” result is recorded and the workflow finishes. No staff task is automatically created. |
| Patient requests a callback on attempt 1 or 2 and provides a time | Nothing is changed in GoTracker. | The next available attempt waits until the requested time. The callback uses one of the three attempts; it does not add an extra attempt. |
| Patient requests a callback but provides no usable time | Nothing is changed in GoTracker. | A local “callback time missing” result is recorded and the workflow finishes. No staff task is automatically created. |
| Patient requests a callback on attempt 3 | Nothing is changed in GoTracker. | A local “callback requested after maximum attempts” result is recorded. There is no fourth call and no automatic staff task. |
| No answer, voicemail, busy line, declined call, or timeout on attempt 1 | Nothing is changed in GoTracker. | The workflow waits for the first retry delay and then makes attempt 2. |
| No answer, voicemail, busy line, declined call, or timeout on attempt 2 | Nothing is changed in GoTracker. | The workflow waits for the second retry delay and then makes attempt 3. |
| No answer, voicemail, busy line, declined call, or timeout on attempt 3 | Nothing is changed in GoTracker. | The patient is recorded locally as unreachable after three attempts. No staff task is automatically created. |
| Patient says not to call again | Nothing is changed in the GoTracker appointment. | The request is recorded locally. When the patient's phone number is available, a do-not-contact rule is added for that clinic location. |
| The call is answered but no recognized decision is returned, or another unexpected result is returned | Nothing is changed in GoTracker. | A local pre-appointment follow-up-needed result is recorded and the workflow finishes. No automatic retry or owned staff task is created. |
| The call cannot be placed because of a calling-service or setup failure | Nothing is changed in GoTracker. | The workflow fails instead of following the normal no-answer retry path. |

Quiet hours and all safety checks are repeated before every retry and callback.

A callback time in the past currently becomes an immediate callback. An invalid
but non-empty callback time can also become immediate. The workflow currently
allows a callback time after the appointment and does not have a separate “latest
allowed pre-appointment call” setting.

## Exactly which GoTracker appointment fields are changed

| Patient decision | Field changed | Fields the workflow does not change |
| --- | --- | --- |
| Confirm | Confirmed checkbox becomes Yes | Appointment status, Preconfirmed checkbox, date, time, provider, room, duration, reason |
| Cancel | Appointment status becomes Cancelled | Confirmed and Preconfirmed checkboxes, date, time, provider, room, duration, reason |
| Reschedule | Start date and start time only | End time, duration, provider, room, reason, status, Confirmed and Preconfirmed checkboxes |
| Callback, no answer, voicemail, busy, declined, timeout, follow-up needed, or do-not-call | No GoTracker appointment field | These results are kept in our system; a do-not-contact rule may also be added locally |

The workflow intentionally does not use GoTracker's “Booked + Waiting” status to
represent confirmation. That status means the patient is physically waiting at
the office, not that the patient confirmed a future appointment.

## How GoTracker changes are completed

For a confirmation, cancellation, or reschedule:

1. Our system sends the requested change to the GoTracker connector.
2. If the connector accepts the request, the workflow finishes with the matching
   success result.
3. GoTracker later reports whether the change was fully applied. If that report is
   missed, the system checks the appointment again after a short delay.

Only one appointment change can be waiting for GoTracker at a time. If the first
request itself fails, the workflow fails. If GoTracker reports a failure only
after the workflow has already finished, the completed workflow is not reopened
and no automatic staff task is created.

## Pausing and stopping the workflow

- A normal pause prevents newly received appointments from starting the workflow
  and delays workflow timers that are already waiting.
- A normal pause is not a guaranteed immediate freeze. A first call that was
  scheduled before the pause, or a call result already returning, may still be
  processed.
- The emergency stop is the stronger control. It blocks new calls and cancels
  active workflow runs.
