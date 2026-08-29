# Workflow Decisions and Gaps to Confirm

## Pre-Appointment Confirmation

1. A GoTracker change is treated as successful when the connector accepts it,
   before final confirmation from the desktop system.
2. “Follow-up needed,” missing callback/reschedule times, and exhausted attempts
   are recorded results, not assigned staff tasks.
3. Rescheduling changes only the start date and time. The workflow does not choose
   a new provider, room, duration, or reason.
4. Callback times are weakly validated and can be after the appointment.
5. If GoTracker cannot be checked immediately before a call, the call is allowed.
6. The workflow depends on the automated calling agent returning the expected
   patient decision. Unrecognized wording goes to follow-up needed.
7. Care-related voice calls currently use the patient's care phone number as
   implied permission unless permission was revoked or a do-not-contact rule
   applies. Each clinic must approve this policy for its locations and laws.
8. The normal pause is not an emergency kill switch.
9. The displayed daily and seven-day campaign limits are not the main limit for
   this workflow. The selected patient voice cooldown is the active cross-workflow
   protection.

## Post-Appointment Follow-Up

1. Post-appointment outreach depends entirely on staff using the Completed Chair
   Flow state consistently.
2. A missing completion time can cause an immediate call instead of stopping the
   workflow.
3. A blank Chair Flow value during the final check does not block the call; only an
   explicit different state does.
4. There is only one patient call attempt.
5. A callback request does not schedule an automatic callback.
6. “Follow-up needed” and “staff handoff” do not create an assigned task or alert.
7. Urgent and non-urgent concerns currently follow the same result path.
8. No post-appointment result is written into the GoTracker appointment or notes.
9. If GoTracker cannot be checked immediately before a call, the call is allowed.
10. The workflow depends on the calling agent returning the exact expected
    “everything is okay” decision. Every other result goes to follow-up needed.
11. Care-related voice calls currently use the patient's care phone number as
    implied permission unless permission was revoked or a do-not-contact rule
    applies. Each clinic must approve this policy for its locations and laws.
