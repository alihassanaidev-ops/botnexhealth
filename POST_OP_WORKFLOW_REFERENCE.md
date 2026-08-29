# Post-Appointment Follow-Up: Current Flow in Plain English

Last reviewed against the current workflow: August 13, 2026

## Purpose

This workflow makes a care follow-up call after a patient has actually completed
an eligible visit. It is not triggered when an appointment is merely booked or
confirmed.

GoTracker remains the main appointment record. Our system uses the completed visit
to schedule the call and records the result of that call.

This document describes what the workflow does today, including the current gaps
that should be agreed with the clinic before launch.

## What triggers post-appointment follow-up

Clinic staff move the patient through GoTracker Chair Flow. The workflow starts
when GoTracker reports that the visit's Chair Flow state became Completed.

The completion time is used as the starting point for the delay. For example, if
the clinic selects a 24-hour delay and staff mark the visit Completed at 3:00 PM on
Monday, the planned call time is 3:00 PM on Tuesday, subject to quiet hours,
cooldown, and the final safety checks.

A blank Chair Flow state does not mean the patient attended. A booked appointment,
a confirmed appointment, or an appointment whose Chair Flow state is blank does
not start this workflow.

The same Completed update is processed only once. If the same appointment is
legitimately marked Completed again at a different completion time, it can create
a new workflow.

## Settings chosen before the workflow is turned on

Each clinic chooses:

1. Which completed appointment reasons are eligible.
2. How many hours after completion to make the call. The default is 24 hours, and
   the clinic may choose zero for an immediate call.
3. The latest time at which a post-appointment call is still useful. The default
   is 72 hours after completion.
4. The patient voice cooldown. The default is 24 hours.
5. Which automated calling profile will speak with the patient.

The latest allowed call time cannot be earlier than the planned call delay or
later than seven days after completion. The clinic's quiet hours, time zone,
consent rules, and do-not-contact rules also apply.

## Complete post-appointment flow

1. Staff mark the visit Completed in GoTracker Chair Flow.
2. The system checks the appointment's first reason against the clinic's eligible
   reason list. Capitalization and extra spaces do not matter, but only the first
   reason is used.
3. If the reason is eligible, the workflow waits until the completion time plus
   the selected delay.
4. Immediately before dialing, the appointment and safety rules are checked again.
5. If another automated workflow recently attempted a voice call to the patient,
   the post-appointment call waits for the patient voice cooldown to end, as long
   as the new time is not later than the clinic's latest allowed call time.
6. If the appointment is still eligible, the system makes one call and waits for
   the result.
7. The result is recorded in our system. The post-appointment workflow does not
   change the GoTracker appointment.

## Checks made before the call

The call is not made when the latest information shows that:

- the appointment is Cancelled, No Show, Office Cancel, or Short Cancel;
- the appointment was moved to a different time;
- GoTracker explicitly shows a different named Chair Flow state instead of
  Completed;
- the appointment's scheduled start time is still in the future;
- the latest allowed post-appointment call time has passed;
- the clinic has used the emergency stop;
- the patient has a do-not-contact rule, no usable phone number, or is not allowed
  to receive this type of care-related call; or
- there is no allowed calling window available.

If it is quiet hours, the call waits until the next allowed time and all checks are
repeated. If the next allowed time is after the post-appointment deadline, the
workflow finishes without calling.

If the current GoTracker appointment cannot be checked because GoTracker is
temporarily unavailable, the workflow currently allows the call to continue. This
policy should be approved by the clinic.

## What patient voice cooldown means for post-appointment calls

The patient voice cooldown is the minimum gap between this call and an automated
voice call attempt made to the same patient by another workflow. A recent attempt
counts even if the patient did not answer.

Unlike the pre-appointment workflow, post-appointment follow-up waits until the
cooldown ends. Before calling, it checks the appointment and safety rules again.
If the cooldown would end after the latest allowed post-appointment call time, the
call is skipped and the workflow finishes.

Setting the cooldown to zero turns this check off.

## Number of call attempts

The current post-appointment workflow makes one patient call attempt. It does not
automatically retry a no answer, voicemail, busy line, declined call, or timeout.
It also does not automatically schedule another call when the patient asks for a
callback.

The workflow waits up to 30 minutes for the analyzed call result. If no result is
received, the attempt is treated as a timeout. A background recovery check also
looks for a result that may have arrived without the normal notification.

## What each call result does

| Call result | GoTracker change | What happens next |
| --- | --- | --- |
| Patient reports that everything is okay | No GoTracker field is changed. | The patient is recorded locally as post-appointment follow-up complete. |
| Patient asks not to be called again | No GoTracker appointment field is changed. | The request is recorded locally. When a phone number is available, a do-not-contact rule is added for that clinic location. |
| Patient reports a concern, requests staff, gives an unclear answer, or returns any other result that is not the expected “everything is okay” result | No GoTracker field is changed. | The patient is recorded locally as needing post-appointment follow-up. The workflow finishes with a staff-handoff label, but it does not create an assigned staff task. |
| No answer, voicemail, busy line, declined call, timeout, or failed call | No GoTracker field is changed. | This is treated as post-appointment follow-up needed. There is no automatic retry and no assigned staff task. |
| Patient asks for a callback | No GoTracker field is changed. | This is treated as post-appointment follow-up needed. No automatic callback or assigned staff task is created. |
| The cooldown cannot end before the latest allowed call time | No GoTracker field is changed. | The workflow finishes without making the call. No follow-up-needed status or staff task is created for this case. |
| The call cannot be placed because of a calling-service or setup failure | No GoTracker field is changed. | The workflow fails. The normal follow-up-needed path is not guaranteed to run. |

There is currently no separate automatic path for an urgent clinical concern. A
concern is recorded as follow-up needed in the same way as other non-okay results.
The clinic must define who reviews it, how quickly, and how emergencies are handled.

## What gets updated

The post-appointment workflow does not update the appointment status, confirmation,
date, time, provider, room, duration, reason, Chair Flow state, or notes in
GoTracker.

When a call reaches a normal patient-result path, it records one of these results
in our system:

- post-appointment follow-up complete;
- post-appointment follow-up needed; or
- do not call requested.

The words “staff handoff” currently describe a workflow result. They do not mean
that a staff member has been assigned a task, notified, or given a deadline.
When the calling window expires before a call, no patient follow-up status is
recorded. A calling-service or setup failure is recorded as a failed workflow
rather than one of the three patient results above.

## Important dependency on clinic behavior

This workflow works only if staff consistently use GoTracker Chair Flow and mark
eligible visits Completed. If staff leave Chair Flow blank or use a different final
state, post-appointment follow-up will not start.

In the clinic data previously reviewed, Chair Flow was blank for most historical
appointments. The workflow should not be enabled until the clinic confirms its
future process and proves it with test visits.

## Missing or invalid completion time

The expected behavior is to schedule from the time the visit became Completed.
However, if GoTracker sends Completed without a usable completion time, the current
workflow does not stop. It can treat the wait as due immediately, and it may not be
able to calculate the latest allowed call time.

This is a current gap. The clinic should confirm that GoTracker always supplies the
completion time, and the workflow should be tested before launch.

A blank Chair Flow state does not start a new post-appointment workflow. However,
after a workflow has already started, a blank or unavailable Chair Flow value at
the final check is treated as unknown rather than as an explicit change away from
Completed. The call may therefore continue. An explicit different state stops it.

## Pausing and stopping the workflow

- A normal pause prevents newly received Completed visits from starting the
  workflow and delays timers that are already waiting.
- A normal pause is not a guaranteed immediate freeze. Work already queued before
  the pause, or a call result already returning, may still be processed.
- The emergency stop is the stronger control. It blocks new calls and cancels
  active workflow runs.

## Recommended acceptance test before launch

Use test patients only and confirm each of these cases:

1. Mark an eligible visit Completed and confirm that one follow-up is scheduled
   from the completion time.
2. Send the same Completed update again and confirm that a duplicate call is not
   scheduled.
3. Cancel or mark the test appointment No Show before the call and confirm that no
   call is made.
4. Change the Chair Flow state away from Completed before the call and confirm that
   no call is made.
5. Test an “everything is okay” result and confirm the local completed result.
6. Test a concern, no answer, and callback request and confirm each becomes local
   follow-up needed without an automatic second call.
7. Test a do-not-call request and confirm later automated calls are blocked for the
   location.
8. Test a recent automated call to the same patient and confirm the cooldown waits
   or expires as expected.
9. Test a Completed visit with a missing completion time and agree whether the
   current immediate behavior is acceptable.
