# Outstanding Scope — Implementation Backlog

**Everything in this document is work that still needs to be built.** Anything already finished has
been removed. Anything outside the agreed scope has been removed.

This document is self-contained. You do not need any other file, specification, or prior context to
work from it. Every item explains what the feature is, why it exists, what already works, what is
missing, and how you will know when you are done.

Work the items in the order given in **Part 9 · Build Order** at the end. The order is not arbitrary —
early items are dependencies of later ones, and the first group protects real patients and real
clinic schedules from being harmed by the software.

---

# Part 0 · Orientation

Read this section once. Everything afterwards assumes you know it.

## What the product does

Clinics — dental practices — run automated patient outreach. The platform places AI voice calls,
sends text messages, and sends emails on a schedule the clinic configures. It reads which patients
need contacting from the clinic's own practice-management software, holds a conversation with the
patient, captures their answer, and writes the result back into that same practice software so the
front desk sees it without re-keying anything.

There are **four campaign types** in scope:

| Campaign | What it does |
|---|---|
| **Appointment Confirmation** | Contacts the patient a configurable number of hours before a visit, confirms they are attending, and records the answer in the practice software |
| **Appointment Reminder** | A short-notice nudge the day before or day of the visit. Runs independently of Confirmation, and must never fire on an appointment that has since been cancelled or moved |
| **Overdue Patient Recall** | Finds patients with no visit in a configured period and no future appointment booked, and re-engages them to book |
| **Sales Qualification** | Receives an inbound enquiry, qualifies the person's intent through the AI agent, and books qualified enquiries onto the right calendar |

## The three systems

The product is built as three separate applications. Knowing which is which matters, because roughly
a third of the remaining work spans two of them.

**1. The Platform.** The main application. Holds the campaign engine, the clinic dashboard, patient
records, compliance controls, and the connections out to the voice, text and email providers. This is
where campaigns are designed, published, and run.

**2. The Cloud Service.** A separate service that sits in front of clinics using the GoTracker
practice-management system. It keeps a continuously updated copy of that clinic's patients,
providers, appointments and schedule, accepts bookings on the clinic's behalf, and holds a queue of
changes waiting to be written into the clinic's own database. It has its own small administrative
web interface used by operators, separate from the clinic dashboard.

**3. The Connector.** A Windows service installed on a computer inside the GoTracker clinic, next to
their database server. It reads changes out of the clinic's database and pushes them to the Cloud
Service, and pulls queued changes down from the Cloud Service and writes them into the clinic's
database. It installs itself, updates itself, and restarts itself if it hangs.

## The two kinds of clinic

Every campaign must work identically on both.

**NexHealth clinics.** Their practice software is reachable through NexHealth, a cloud service that
sits between many dental systems and the outside world. The Platform talks to NexHealth directly over
the internet. Reads and writes are immediate.

**GoTracker clinics.** Their practice software has no cloud interface at all — the data lives on a
database server physically inside the practice. The Platform talks to the Cloud Service, which talks
to the Connector, which talks to that database. Because the clinic's computer can be switched off,
asleep, or disconnected, **a write may not complete immediately.** This is the single most important
architectural fact in this document, and most of Part 1 exists because of it.

## How a booking travels to a GoTracker clinic

Follow this path; several items below sit on it.

1. The Platform asks the Cloud Service to book an appointment.
2. The Cloud Service checks the requested time against its own copy of the clinic's schedule — is it
   inside the provider's working hours, and does it clash with an existing appointment? If it passes,
   it records the appointment in its own copy, marks it **waiting to be written to the practice
   software**, and tells the Platform the booking succeeded.
3. Some time later — seconds, or hours if the clinic's computer was off — the Connector asks the
   Cloud Service for anything waiting to be written.
4. The Connector writes the appointment into the clinic's database.
5. The Connector reports back whether the write succeeded or failed.
6. The Cloud Service records the real appointment number the practice software assigned, or counts
   the attempt and puts the write back in the queue to retry (up to five attempts).

**Steps 1, 2, 3, 5 and 6 are built and working well.** Step 4 currently writes without checking
anything first, and step 2 tells the Platform "confirmed" when the appointment has not yet reached
the practice. Items 1 to 5 in Part 1 close exactly that gap.

## Vocabulary used in this document

| Term | Meaning |
|---|---|
| **Campaign** | One of the four automated outreach programmes above, configured for one clinic location |
| **Run** | One patient's journey through one campaign — enrolled, contacted, answered, finished |
| **Step** | One action inside a campaign — send a text, place a call, wait, branch on the answer, exit |
| **Enrolment** | The moment a patient is added into a campaign |
| **Compliance gate** | The set of checks run immediately before every single message, which decide whether contacting this person right now is permitted |
| **Quiet hours** | The window during which a clinic may not contact patients. A message that becomes due inside it is **held until the window opens**, never dropped and never sent early |
| **Revalidation** | Re-checking, immediately before contacting someone, that the reason for contacting them is still true — the appointment still exists, at the time we think, and has not been cancelled |
| **Suppression** | A patient who must not be contacted — opted out, on the do-not-contact list, or without the required consent |
| **Write-back** | Sending a result (a confirmation, a cancellation, a new booking) from the platform into the clinic's practice software |
| **Operator** | Your internal staff who set clinics up and support them — distinct from clinic staff |

---

# Contents

**Part 1 · GoTracker booking safety** — items 1–10
**Part 2 · Campaign engine and messaging** — items 11–21
**Part 3 · The four campaigns** — items 22–24
**Part 4 · Expanded NexHealth integration — the six data families** — items 25–31
**Part 5 · Compliance, monitoring and operations** — items 32–40
**Part 6 · Testing, delivery and documentation** — items 41–47
**Part 7 · Decisions needed before some items can start**
**Part 8 · What is already built — do not rebuild**
**Part 9 · Build order**

---

# Part 1 · GoTracker booking safety

This group is first because everything in it protects a real clinic's schedule and a real patient's
expectations. The GoTracker path is otherwise in good shape — the queue, the retry ladder, the
reconciliation, the heartbeats and the auto-updating installer are all built. What is missing is the
safety of the final write, and honesty about when a booking is actually real.

---

## Item 1 · Check the clinic's schedule before writing a booking into it

### Description

When a booking has been waiting in the queue — possibly for hours, because the clinic's computer was
switched off overnight — the Connector must confirm the booking is still safe to make *before* it
writes it into the practice's database. Right now it writes without looking.

### Context and expected behaviour

The whole reason the Cloud Service exists is that a GoTracker clinic can be unreachable. A patient
can call the AI agent at 9pm, be offered a slot, and accept it, while the practice's computer is off.
The booking waits in the queue until the practice opens.

But the practice is not frozen while it waits. A staff member arriving at 8am may book that same
slot for a walk-in. When the Connector comes online at 8:15am and blindly writes the queued booking,
it puts two patients in one slot — and nobody finds out until both turn up.

The same applies to the patient record. If the queued booking references a patient who has since
been merged, deleted, or renumbered in the practice software, writing it produces an appointment
attached to the wrong person or to nothing at all.

Expected behaviour: immediately before writing, the Connector re-reads the clinic's live database
and satisfies itself on three points. Only if all three pass does it write.

### Current implementation status

**Not implemented.** The Connector's booking write-back loop performs exactly one check before
writing — it opens a database connection and runs a trivial query to confirm the database is
reachable. That is a connectivity test, not a safety check. It proves the database is up; it proves
nothing about whether the booking is still valid.

Having confirmed only that, the Connector calls the practice software's appointment-creation
procedure directly. There is no query of the current schedule, no verification of the patient, and no
check for an appointment that already matches.

### Remaining work

Before each queued booking is written, re-read the clinic's live database and confirm:

1. **The slot is still free** — the provider and schedule column are unoccupied for the requested
   date, time and duration.
2. **The patient still resolves** — the patient reference on the queued booking still points to the
   intended person in the practice software.
3. **It is not already there** — no equivalent appointment exists, including one this Connector may
   already have written (see Item 3).

If all three pass, write as it does today. If any fails, do not write — record it as a conflict
(Item 2).

### Watch out for

The check and the write must be close together to mean anything. A check performed a minute earlier
proves nothing. Run both against the same open connection, back to back.

Do not hold a long-running lock on the practice's live database. Clinic staff are using it while you
work, and a lock held across a slow write will be noticed at the front desk immediately. Read, decide,
write, release.

### Acceptance criteria

- A booking queued while the clinic was offline, whose slot was taken by staff in the meantime, is
  **not** written and is recorded as a conflict.
- A booking whose patient reference no longer resolves is **not** written and is recorded as a
  conflict.
- A booking equivalent to one already present in the practice software is **not** written a second
  time.
- A booking that passes all three checks is written exactly as it is today, with no change in
  behaviour or timing.
- Neither the check nor the write holds a database lock long enough to be noticeable to clinic staff
  during normal use.

---

## Item 2 · A conflict outcome for writes that must not proceed

### Description

When Item 1 decides a booking is unsafe to write, there must be somewhere to record that decision, a
way for an operator to see it, and a way for a human to resolve it. Today there is no such state.

### Context and expected behaviour

There is an important difference between two situations that currently look identical:

- **Failed** — we tried to write and something went wrong. A transient error. Retrying is correct and
  will probably succeed.
- **Conflict** — we deliberately did not write, because writing would have caused harm. Retrying is
  exactly the wrong response; it would keep trying to double-book a slot forever.

Every queued write currently ends up in one of three states: waiting, written, or failed. A failed
write is automatically retried while attempts remain. If Item 1 marked an unsafe booking as failed,
the system would retry it — repeatedly attempting the harmful write it just prevented.

Expected behaviour: a conflict is its own final state. It is never retried automatically. It records
which check failed and why. It appears in the operator's administrative interface with enough
information to understand and act on it, and an operator can resolve it — cancel the booking, or
correct the underlying problem and put it back in the queue deliberately.

### Current implementation status

**Not implemented.** The write-status vocabulary in the Cloud Service has exactly three values:
waiting, written, failed. The endpoint the Connector reports results to accepts only "written" or
"failed" and rejects anything else outright.

The Platform mirrors the same three states on its side, so even if the Cloud Service produced a
conflict, the Platform could not represent it.

There is no operator screen for reviewing conflicts, because there are none to review.

### Remaining work

- Add a conflict state to the write-status vocabulary in the Cloud Service, and to the matching
  record on the Platform side.
- Store the reason — which of Item 1's three checks failed, and enough detail to act on it (for
  example, "slot occupied by appointment #12345 for a different patient").
- Accept the new state on the endpoint the Connector reports to, on its own branch that settles
  immediately and does not feed into the retry ladder.
- Surface conflicts in the Cloud Service's operator interface, listing the appointment, the patient,
  the reason and when it happened.
- Give an operator two resolutions: cancel the booking, or requeue it after fixing the cause. Record
  who resolved it and how.

### Watch out for

The retry path currently returns a failed write to the queue while attempts remain. A conflict must
not take that path. If it does, you have rebuilt the exact problem Item 1 exists to prevent.

### Acceptance criteria

- A conflict is a distinct state, visibly different from failed, and is never retried automatically.
- The recorded conflict names which check failed and gives enough detail for an operator to
  understand it without opening a database.
- Conflicts are visible in the operator interface, per clinic, with patient, appointment and reason.
- An operator can cancel or requeue a conflict, and that action is recorded against their identity.
- The Platform can see the conflict state, so a campaign can react to it rather than assuming success.

---

## Item 3 · Prevent the same booking being written twice

### Description

The Connector writes a booking into the practice's database, then reports back that it succeeded. If
it stops between those two actions, the booking has been written but nobody knows — and it will be
written again on the next cycle, putting a duplicate appointment in the practice's schedule.

### Context and expected behaviour

The write and the acknowledgement are two separate operations. Between them, the Connector can stop
for entirely ordinary reasons: the service restarts, Windows applies an update, the watchdog
restarts a hung process, the network drops, the clinic's computer is switched off at the wall.

When that happens, the Cloud Service never hears back, so the booking is still marked as waiting.
Next cycle, it is handed to the Connector again, which writes it again. The practice now has the same
patient booked twice for the same slot. Nothing in either system detects or corrects this.

Expected behaviour: each queued write carries a stable identifier that travels with it and is stored
alongside the appointment in the practice software. Before writing, the Connector checks whether that
identifier is already present. If it is, the write already happened — the Connector simply reports
the existing appointment number and moves on. Writing the same booking twice becomes impossible
rather than unlikely.

The identifier must be **derived from the booking itself**, not randomly generated. A retry after a
lost response has to arrive at the same identifier, or it proves nothing.

### Current implementation status

**Not implemented.** No such identifier exists anywhere on the write path. The Cloud Service does
give each cloud-created appointment its own reference number, but that number is used only to match
the acknowledgement back to the queue entry — it is never written into the practice software, and
nothing checks for it before writing.

This gap is the direct cause of the duplicate scenario above, and it is the reason Item 5 exists.

### Remaining work

- Generate a stable identifier for each queued write, derived from the booking's own details so that
  recomputing it always produces the same value.
- Include it when the Cloud Service hands work to the Connector.
- Store it in the practice software alongside the appointment.
- Have the Connector look it up before writing, and treat a match as "already done" rather than
  writing again.

### Watch out for

The practice software's database belongs to the vendor and you cannot add columns to it. Store the
identifier somewhere you legitimately can — the appointment's comment or note fields have supported
write procedures — or keep a mapping on the Connector's own side. If you choose the Connector's own
side, that record must survive both a restart and a software upgrade, which is Item 5.

### Acceptance criteria

- Every queued write carries an identifier derived from its own content, not a random value, and
  recomputing it produces the same result every time.
- Stopping the Connector between the write and the acknowledgement, then restarting it, produces
  **no** second appointment in the practice software.
- Re-processing an already-written booking returns the original appointment number rather than
  creating a new one.
- The identifier survives a Connector restart and a Connector version upgrade.
- This behaviour is proven by an automated test that actually kills the process mid-write.

---

## Item 4 · Tell the caller when a booking is not yet real

### Description

When the Cloud Service accepts a booking for a GoTracker clinic, it reports plain success — even
though the appointment has not yet reached the practice's software and may not for hours. The
Platform believes it, and tells the patient the appointment is confirmed.

### Context and expected behaviour

For a NexHealth clinic, a successful booking response means the appointment is genuinely in the
practice's system. For a GoTracker clinic it means something much weaker: the request has been
accepted and queued. The appointment may still be waiting, may hit a conflict (Item 2), or may
exhaust its five attempts and never arrive at all.

Because the two look identical to the Platform, a patient can be told "you're booked for Tuesday at
2pm" when the practice will never see it. The patient arrives; the clinic has no record; the clinic
blames the platform.

Expected behaviour: the booking response says which situation it is. If the write to the practice
software has not been confirmed, the response says so, and the Platform treats that as a distinct
outcome — not a success and not a failure — with wording to the patient that matches ("we're
confirming this with the practice and will let you know").

### Current implementation status

**Not implemented, and actively misleading.** The Cloud Service's booking response contains the new
appointment reference and related details, but no field at all describing whether the practice-software
write has landed — despite the record having just been marked as waiting.

On the Platform side, the code that interprets the response falls back to reporting the booking as
**scheduled** whenever no status is supplied. Because the Cloud Service never supplies one, every
GoTracker booking is reported as scheduled the instant it is accepted.

There is a second, smaller problem in the same place: the human-readable message that accompanies
the result is fixed text asserting the appointment was booked successfully, regardless of the actual
status. Even once a pending status starts arriving, that message would still claim success.

### Remaining work

- Add a write-status field to the Cloud Service's booking response, and to the responses for
  rescheduling, confirming and cancelling. It must distinguish "confirmed in the practice software"
  from "accepted, not yet written".
- Update the Cloud Service's published interface documentation to match.
- On the Platform, handle the pending status as its own outcome, distinct from both success and
  failure, and make it visible in the campaign run history.
- Fix the accompanying message so it follows the status rather than always claiming success.

### Watch out for

This changes an interface the Platform already relies on. Add a new field rather than changing the
meaning of an existing one, and land the Platform's handling of the pending status **first**, so that
by the time the Cloud Service starts sending it, there is something ready to receive it.

### Acceptance criteria

- Every booking, reschedule, confirm and cancel response carries a write-status field.
- A booking whose practice-software write has not landed reports pending, never scheduled.
- The Platform distinguishes pending from confirmed in the campaign run history and in what it tells
  the patient.
- The message accompanying a pending result no longer claims the booking succeeded.
- The Cloud Service's published interface documentation matches the actual behaviour.

---

## Item 5 · Recover in-flight writes after the Connector restarts

### Description

The Connector currently keeps no record of what it was doing. If it stops mid-task, that work is
invisible to it when it comes back. This is a deliberate design choice that needs either completing
or formally closing.

### Context and expected behaviour

The original design called for the Connector to keep its own durable record of writes in progress
and acknowledgements received, so that after any restart it could work out what it had already done
and carry on safely.

The current design instead treats the Cloud Service's queue as the only durable record. That is a
reasonable decision — the cloud queue genuinely is durable, and keeping patient data off the clinic's
machine is a real advantage. But it leaves the window described in Item 3: between writing and
acknowledging, the Connector holds knowledge that exists nowhere else, and losing it causes a
duplicate booking.

Expected behaviour: after any restart, the Connector can determine whether a write it had begun
actually completed, and neither repeats it nor loses it.

### Current implementation status

**Deliberately not implemented.** The Connector holds no local record of in-flight work; the design
note in the code states plainly that the cloud is treated as the durable queue.

### Remaining work

Choose one of two approaches and record the decision and its reasoning:

**Either** give the Connector a durable local record of in-flight writes and acknowledgements, which
it reconciles against the Cloud Service on startup;

**or** rely entirely on Item 3's identifier, and ensure that identifier is stored somewhere that
survives both restart and upgrade — which in practice means a small amount of local state anyway.

Do not do both. Pick one, implement it, and write down why.

### Watch out for

Anything stored on the clinic's machine that includes patient information must be encrypted at rest.
The Connector currently stores no patient data locally, so introducing an unencrypted local store
would be a step backwards on privacy. If you store anything, encrypt it.

### Acceptance criteria

- Stopping the Connector mid-write and restarting it produces neither a duplicate appointment nor a
  lost booking.
- Any local state introduced is encrypted at rest.
- Any local state survives a Connector version upgrade.
- Backup and restore of that state is documented — or, if the decision is to hold no local state,
  that decision and its reasoning are written down.

---

## Item 6 · Alert when a clinic's connection is unhealthy

### Description

Nothing currently notifies anyone when a clinic's Connector stops working, when the queue of pending
writes backs up, or when writes are repeatedly failing. These are discovered when the clinic phones.

### Context and expected behaviour

The Connector reports in regularly with a heartbeat carrying its status, its version, how many items
are waiting in its queue, and how recently it last synchronised. All of that is stored. None of it is
watched.

Expected behaviour: when a clinic's connection degrades, the team finds out from an alert rather than
from the clinic. Six conditions matter:

- **Connector offline** — a clinic that should be reporting in has stopped
- **Stale reads** — the Connector is running but has not successfully read new data for too long
- **Stale writes** — the Connector has not successfully written for too long
- **Queue backlog** — the number of writes waiting has grown beyond normal
- **Repeated write failures** — writes are failing or hitting conflicts at an abnormal rate
- **Repeated database errors** — the Connector is logging errors against the clinic's database

### Current implementation status

**Not implemented for the sync path.** The Cloud Service does have working alerting — an alert
channel with email delivery, and five alarms already wired to it. But every one of those five watches
infrastructure: whether the service is running, whether it is returning errors, and the database
server's disk, processor and connection count.

Not one of them watches whether clinics are actually syncing. The heartbeat data needed is already
collected and stored; nothing consumes it.

### Remaining work

- Publish the sync-health figures already collected — connector last-seen time, queue depth, last
  successful read, last successful write, failure counts — as monitored metrics.
- Add an alarm for each of the six conditions above, routed to the alert channel that already exists.
- Choose thresholds from real observed values in the staging environment rather than guessing.

### Watch out for

Silence from a clinic has two meanings. A practice that closes at 6pm and reopens at 8am is not an
incident, and an alarm that pages someone every night will be switched off within a week and will
then protect nothing. Account for clinic operating hours so that only *unexpected* silence alerts.

### Acceptance criteria

- Each of the six conditions has an alarm delivering to the existing alert channel.
- Each alarm has been deliberately triggered in staging and confirmed to arrive.
- A clinic being closed overnight or at the weekend does not raise an alert.
- Every threshold is documented with the reasoning behind the number chosen.

---

## Item 7 · Complete the connection health screen

### Description

Operators have a screen showing some of a clinic's connection health. Several of the most useful
fields are collected but not displayed.

### Context and expected behaviour

When a clinic reports a problem, an operator should be able to answer "is their connection healthy?"
from one screen without asking an engineer to query a database.

The full picture is: is the Connector online, which version is it running, when did it last report
in, when did it last successfully read, when did it last successfully write, how many items are
waiting to be written, how many writes have failed, and how many are sitting in conflict.

### Current implementation status

**Partially implemented.** The Cloud Service's operator interface has an agent health panel showing
whether the Connector is online, its status, its version and when it last reported in. There is also
a dashboard summary and a log view of faults reported by Connectors.

Missing from the display: last successful read time, last successful write time, queue depth, failed
write count, and conflict count. Queue depth and sync timing are already stored in the heartbeat
record — they are simply not shown. Conflict count depends on Item 2.

### Remaining work

Extend the health panel and the data behind it to show the five missing fields. This is largely
display work over data that already exists.

### Acceptance criteria

- All nine health fields are visible per clinic location on one screen.
- Queue depth and failed-write count reflect live values, not cached ones.
- Conflict count appears once Item 2 is complete.
- An operator can answer "is this clinic's connection healthy?" without leaving the screen.

---

## Item 8 · Mapping review before a clinic can take live bookings

### Description

Before a GoTracker clinic goes live, an operator must confirm that the clinic's providers, treatment
reasons, appointment types, schedule columns and appointment statuses have been correctly matched
between the practice software and the platform. Until that confirmation exists, the clinic must not
be able to take live bookings.

### Context and expected behaviour

Every practice names things differently. One clinic's "Hygiene" is another's "Recall Visit". Their
schedule columns may be named after rooms, or after staff, or after nothing meaningful. Appointment
statuses vary by practice.

If these are mapped incorrectly and bookings go live, patients get booked with the wrong provider,
into the wrong room, for the wrong treatment length. This is not a subtle failure — it disrupts a
working practice.

Expected behaviour: a review screen shows every mapping for a location side by side. An operator
checks them and confirms. Until that confirmation is recorded, any booking attempt for that location
is refused with a clear explanation. This is a checklist step during clinic onboarding, immediately
before go-live.

### Current implementation status

**Not implemented anywhere.** Neither the Cloud Service's operator interface nor the clinic dashboard
has a mapping review screen, and no check anywhere prevents a location from taking bookings. A
location can accept live patient bookings the moment its credentials are saved, with nobody having
confirmed that anything is mapped correctly.

### Remaining work

- Build a review screen listing, for one clinic location: mapped providers, appointment reasons and
  types, schedule columns, and appointment status mappings.
- Add an explicit confirmation action, recording who confirmed and when.
- Refuse booking requests for a location with no confirmation on record, returning a clear reason.
- Surface the same gate in the Platform's pre-launch checklist so it is visible to whoever is taking
  the clinic live.

### Watch out for

The gate must refuse by default. A location with no confirmation record must be blocked, not allowed.
If an unconfirmed location can book, the control is decorative.

### Acceptance criteria

- The review screen shows every category of mapping for a chosen location.
- An operator can confirm the mapping, and the confirmation is recorded against their identity with a
  timestamp.
- A booking attempt for an unconfirmed location is refused, with an error explaining what to do.
- A booking attempt for a confirmed location succeeds unchanged.
- The Platform's pre-launch checklist shows whether the gate has been passed.

---

## Item 9 · Sign the messages the Connector sends to the Cloud Service

### Description

The Connector proves who it is when it calls the Cloud Service, but does not prove that the contents
of its message were not altered along the way.

### Context and expected behaviour

Two different protections are needed on this link. **Authentication** answers "is this really the
Connector for clinic X?" **Integrity** answers "is this the message they actually sent?" The first is
in place. The second is not.

The traffic carries patient data and instructions to write into a clinic's records, so both are
required.

Expected behaviour: the Connector signs the contents of each message using its own installation
secret. The Cloud Service verifies that signature as well as the identity, and rejects any message
whose contents do not match its signature.

### Current implementation status

**Partially implemented.** Authentication is done properly — each installation has its own key,
issued per clinic with defined permissions, and operators can rotate keys from the administrative
interface. Messages the Cloud Service sends outward to the Platform **are** signed, using an existing
signing mechanism.

The Connector-to-Cloud direction carries only the identifying key. There is no signature over the
message contents.

### Remaining work

Sign the message contents on the Connector side and verify on the Cloud Service side, reusing the
signing approach already built for outbound messages rather than introducing a second scheme.

### Watch out for

Connectors update themselves, but not instantly. If the Cloud Service starts requiring signatures
before every clinic has updated, those clinics stop syncing. Accept both signed and unsigned messages
during a transition window, confirm the whole fleet has updated, then require signatures.

### Acceptance criteria

- Messages from the Connector carry a signature over their contents.
- The Cloud Service rejects a message with a valid identifying key but an invalid signature.
- Replaying a previously captured message is rejected.
- No clinic loses connectivity during the rollout.

---

## Item 10 · Operator runbooks for the GoTracker path

### Description

Written procedures for the five situations an operator will actually face on the GoTracker path.
None currently exist.

### Context and expected behaviour

When something goes wrong at 9am on a Monday, whoever is on support needs a document that tells them
what to check and what to do — not a codebase to read.

The five situations are:

- **Queue replay** — writes are backed up and need to be worked through safely
- **Duplicate booking investigation** — a practice reports the same patient booked twice, and someone
  must determine how it happened and clean it up
- **Clinic machine outage** — the practice's computer has been off or unreachable for an extended
  period
- **Database unavailable** — the Connector is running but cannot reach the practice's database
- **Sync drift** — the platform's copy of the clinic's data has diverged from reality

### Current implementation status

**Not implemented.** Installation and setup documentation is genuinely good — build instructions,
installation guide, quick start, and an interface reference all exist. None of the five operational
runbooks does.

### Remaining work

Write one runbook per situation. Each should state which alarm from Item 6 signals it, the steps to
diagnose it, the steps to fix it, and how to confirm recovery.

### Acceptance criteria

- A runbook exists for each of the five situations.
- Each names the alarm that triggers it and the signal that confirms recovery.
- Each can be followed by someone who did not write it, without reading source code.
- The duplicate-booking runbook explicitly covers the scenario in Item 3.

---

# Part 2 · Campaign engine and messaging

---

## Item 11 · A booking step inside campaigns

### Description

Campaigns can contact a patient and capture their answer, but cannot book an appointment. There is no
booking step available when building a campaign.

### Context and expected behaviour

A campaign is built as a sequence of steps — send a text, place a call, wait for a reply, branch on
the answer, exit. The available steps cover messaging, waiting, branching, and updating a patient's
status or an existing appointment.

There is no step that books a new appointment. This is a hard blocker on two of the four campaign
types:

- **Overdue Recall** exists to bring lapsed patients back. Its entire purpose is converting a
  conversation into a booking. Without a booking step it can only send a link and hope.
- **Sales Qualification** must book qualified enquiries onto the right calendar. Without a booking
  step it can qualify someone and then do nothing with them.

Expected behaviour: a booking step that can be placed in any campaign, configured with which
appointment type and provider to book, and which works identically on both kinds of clinic.

### Current implementation status

**Implemented as an engine/builder capability.** Campaign definitions can now include a
PMS-neutral `book_appointment` node. It resolves the patient from the enrolled contact, renders
provider/type/time from campaign context, re-checks live PMS availability immediately before writing,
books through the shared `PMSAdapter.book_appointment` contract, records campaign write provenance,
updates the run's appointment reference, and emits workflow-channel reporting events.

The node exposes three required branches: booked, could not book, and **pending** — the GoTracker
case where the Cloud Service accepted the booking before the clinic machine has confirmed write-back.

### Remaining work

- Use the node in the Overdue Recall and Sales Qualification campaign templates.
- Decide the exact pending-branch campaign policy/copy for those templates under Decision B.

### Watch out for

Booking has a real-world effect on a clinic's schedule. Two protections that already exist elsewhere
in the engine must apply here:

- **Do not book twice.** Message-sending steps are already protected against being run twice for the
  same patient at the same point in a campaign. Booking needs the same protection, or a retried task
  will double-book.
- **Re-check before booking.** Messaging steps already re-verify the situation immediately before
  sending. Booking must do the same — the slot may have gone while the patient was deciding.

Whether a pending booking should pause the campaign and wait for confirmation, or exit and hand the
patient to staff, is still an open campaign-design decision — see **Decision B** in Part 7. The node
does not bake that policy into runtime; it routes to the authored `pending_next_node_id`.

### Acceptance criteria

- A campaign can include a booking step, configured in the builder like any other step.
- The step books successfully on both a NexHealth clinic and a GoTracker clinic, with no
  clinic-type-specific configuration.
- A step retried after a failure never produces a second appointment.
- A slot that has been taken since the conversation began is detected and routed down the
  could-not-book branch instead of being booked.
- Pending is a distinct branch, visible in the campaign run history and counted separately in
  reporting.

---

## Item 12 · Generate the booking, confirmation and reschedule links

### Description

Campaign messages can include a booking link, a confirmation link or a reschedule link. The
placeholders exist and messages already use them, but nothing generates the actual links.

### Context and expected behaviour

Campaign message wording supports placeholders — the patient's first name, the clinic name, the
appointment date, and so on — which are filled in at send time. Three of those placeholders are
links: one for booking, one for confirming, one for rescheduling. A patient receives a text saying
"book here" followed by a working link personal to them.

Expected behaviour: when a campaign reaches a step whose message contains one of these placeholders,
the system generates a link that is unique to that patient and that campaign run, works when clicked,
and expires after a sensible period.

### Current implementation status

**Not implemented, and currently unsafe.** All three placeholders are defined and available to
message authors. Six campaign templates already use the booking link in patient-facing wording.

**Nothing in the system ever generates the value.** The placeholders read from a piece of run
information that is never populated, so any message using one would go out to a patient with the link
missing.

This has not caused harm yet only because those six templates are not currently switched on for
clinics. It becomes a live problem the moment more templates are enabled — which is Item 22.

### Remaining work

- Build link generation producing a unique, signed, expiring link per patient per campaign run.
- Populate the link before the first step whose message needs it.
- Make campaign publishing fail when a message uses a link placeholder that cannot be generated for
  that clinic, rather than allowing it through.

### Watch out for

These links are sent to patients and opened without logging in. They must be signed so they cannot be
forged, scoped to a single campaign run, and expire. The page they open must not reveal any patient
information beyond what the patient already knows. Never put a raw patient identifier in the link
itself.

### Acceptance criteria

- A message containing a link placeholder is delivered with a working, personalised link.
- The link opens the correct patient's booking, confirmation or reschedule context.
- An expired link fails cleanly with an explanatory message.
- A tampered link is rejected.
- Publishing a campaign whose messages use a link that cannot be generated for that clinic fails,
  naming the problem.

---

## Item 13 · Enforce readiness checks when a campaign is published

### Description

Each campaign declares what it needs in order to work — a configured location, text messaging set up,
email set up, patient consent recorded, quiet hours defined, particular practice data available. These
are currently checked and reported as warnings, but nothing stops a campaign being published without
them.

### Context and expected behaviour

Publishing a campaign makes it live for a clinic. A campaign that needs text messaging, published for
a clinic with no text messaging configured, will fail silently for every patient it enrols.

Expected behaviour: publishing fails when a declared requirement cannot be met, naming the specific
requirement and how to satisfy it. Warnings remain warnings; declared requirements become blockers.

### Current implementation status

**Partially implemented.** The checks themselves exist and work — a readiness service can evaluate
whether a clinic has messaging configured, consent recorded and so on, and the pre-launch checklist
screen uses it to show the clinic's state.

The publishing path, however, uses a placeholder version of this check that deliberately does nothing
and raises no issues. The real check is never consulted at publish time. The code comments describe
the current behaviour as advisory by design, pending exactly this work.

### Remaining work

Replace the placeholder with the real readiness check on the publishing path, and make a failed check
block publication rather than warn.

### Watch out for

This will block publishes that currently succeed. In particular, once Item 12 introduces a booking
link requirement, templates using booking links will fail to publish until link generation exists.
Sequence this with Items 11 and 12 rather than ahead of them.

### Acceptance criteria

- A campaign whose declared requirements cannot be met for a clinic cannot be published for that
  clinic.
- The failure names the specific unmet requirement and what to do about it.
- A campaign whose requirements are met publishes exactly as before.
- Advisory warnings still appear as warnings and do not block.

---

## Item 14 · Retry text messages that fail for temporary reasons

### Description

If sending a text message fails, the attempt is abandoned. There is no retry and no distinction
between a temporary problem and a permanent one.

### Context and expected behaviour

Message sending fails in two very different ways. **Temporary** — the carrier is briefly unavailable,
a rate limit was hit, the network hiccuped. Retrying shortly afterwards works. **Permanent** — the
number is invalid, the recipient has blocked messages, the number is a landline. Retrying will never
work and simply wastes sending quota.

The email side already does this correctly: it classifies the failure, retries only the temporary
class with a growing delay between attempts, and deliberately does not retry permanent failures.

Expected behaviour: text messaging behaves the same way.

### Current implementation status

**Not implemented for text messages.** The text-sending step has no retry logic and no failure
classification — a single temporary error ends the attempt for that patient.

Separately, no unique send identifier is passed to the messaging provider, which matters as soon as
retry is introduced.

### Remaining work

- Classify text-sending failures as temporary or permanent.
- Retry only the temporary class, with a growing delay, up to a limit.
- Record the final classification against the attempt so it is visible in the campaign run history.
- Send a unique identifier with each message to the messaging provider, so that a retry of a message
  that actually went out cannot deliver it twice.

### Watch out for

Adding retry **without** the unique send identifier makes things worse, not better — you convert a
missed message into a duplicate message. Build both together.

The engine already prevents the same campaign step running twice for the same patient. Confirm that
retries within a single attempt do not trip that protection, while a retry after a crash still does.

### Acceptance criteria

- A temporary failure is retried with a growing delay; a permanent failure is not retried.
- A retry never results in the patient receiving two copies of the message.
- The final outcome and its classification are visible in the campaign run history.
- Behaviour matches the email side.

---

## Item 15 · Feed message delivery results back into campaigns

### Description

The messaging provider reports whether each message was actually delivered. Those reports are received
but never reach the campaign, so a message that was accepted and then failed to arrive still counts
as a successful contact.

### Context and expected behaviour

Accepting a message for delivery and delivering it are different things. A message can be accepted by
the carrier and then fail — the handset is off, the number is disconnected, the carrier filters it.

Expected behaviour: the delivery result updates the record of that contact attempt. A message that
was never delivered is not counted as a successful contact in reporting, and a campaign can branch on
delivery failure — for example, try email instead.

### Current implementation status

**Partially implemented.** Delivery reports from the messaging provider are received and processed —
they feed the usage and cost tracking, which works.

They are not connected to the campaign. The record of the contact attempt is never updated with the
delivery result, the campaign cannot react to a delivery failure, and reporting counts an undelivered
message as a delivered one.

### Remaining work

- Update the contact attempt record when a delivery report arrives.
- Distinguish delivered from undelivered in campaign reporting.
- Allow a campaign to branch on a hard delivery failure.

### Acceptance criteria

- A delivery report updates the corresponding contact attempt.
- Reporting distinguishes messages delivered from messages merely accepted.
- A campaign can route a patient down a different branch when a message hard-fails.

---

## Item 16 · Stop contacting a patient on other channels once they reply

### Description

When a patient responds through one channel, the remaining steps of that campaign on other channels
should stop. Today this depends on whoever designed the campaign having drawn that branch.

### Context and expected behaviour

A patient who confirms their appointment by text should not then receive a confirmation phone call
an hour later. It is the single most common complaint about automated outreach, and it makes the
clinic look disorganised.

Expected behaviour: once a patient's response is recorded, the engine itself stops the remaining
steps of that run on every channel — regardless of how the campaign was drawn. A campaign author
should not be able to create this problem by omission.

### Current implementation status

**Partially implemented.** Suppression based on lists works well — do-not-contact, opt-outs, consent
and unsubscribes are all enforced before every message. Patient replies by text and email are
received and can resume or redirect a campaign, and the two currently live campaigns branch correctly
on them.

But that correct behaviour is a property of **how those two campaigns were drawn**, not a guarantee
from the engine. A campaign that omits the branch gets no protection.

### Remaining work

Add an engine-level rule, applied during the pre-send check: if a response from this patient has been
recorded during this run, suppress subsequent sending steps on all channels — unless the campaign
explicitly opts out of that behaviour.

### Acceptance criteria

- A patient replying by text is not subsequently emailed or called by the same campaign run.
- The rule applies even when the campaign does not explicitly branch on the reply.
- An explicit opt-out exists for the cases where a deliberate follow-up is wanted.
- The two currently live campaigns behave exactly as they do today.

---

## Item 17 · Stop calling a service that is failing

### Description

When an outside service — the practice software connection, the voice provider, the messaging
provider, the email provider — starts failing, the system keeps sending requests into it. There is no
mechanism to back off and recover.

### Context and expected behaviour

When an outside service goes down, continuing to hammer it achieves nothing, slows everything else
down while requests time out, and can extend the outage or trigger rate limiting that delays recovery.

Expected behaviour: after repeated failures against a given service for a given clinic, the system
stops calling it and holds the affected work instead. Periodically it lets one request through to test
whether the service has recovered. If that succeeds, normal operation resumes automatically.

Critically, work blocked this way is **held and retried later**, exactly as a message due during quiet
hours is held until morning. Nothing is dropped and no campaign run fails because a supplier had an
outage.

### Current implementation status

**Not implemented.** Several related protections exist and are good, but none of them is this:

- Requests to the practice-software cloud service are rate-limited across the whole fleet, and
  respond correctly to being told to slow down.
- Voice call errors are classified three ways, and the genuinely ambiguous class is deliberately not
  retried, to avoid calling a patient twice.
- There is a health check on the practice-data read path that skips a send when reads are unhealthy.

All of those act on a single request. What is missing is the layer that remembers a service has been
failing and stops trying for a while.

### Remaining work

- Build a failure-tracking mechanism per service and per clinic, shared across all running instances
  of the application.
- Stop sending requests once failures pass a threshold, hold the affected work with a retry time, and
  probe periodically for recovery.
- Raise an alert when a service is cut off, and when it recovers.

### Watch out for

The application runs as multiple copies simultaneously. If each copy tracks failures independently,
they will disagree about whether a service is down, and the protection becomes unreliable. Keep the
state shared.

Follow the existing convention for the shared store: if the shared store itself is unavailable, allow
traffic through rather than blocking everything. An outage in the safety mechanism must not become a
total outage.

### Acceptance criteria

- Repeated failures against a service cause the system to stop calling it within a defined threshold.
- Work blocked this way is held with a retry time; no campaign run fails as a result.
- Recovery is detected automatically and normal operation resumes without a restart.
- All running copies of the application agree on the state.
- Unavailability of the shared store allows traffic through, with a warning logged.
- Cut-off and recovery both raise alerts.

---

## Item 18 · Limit how fast and how many messages and calls go out

### Description

There is no limit on how many calls a single clinic can have in progress at once, and no limit on how
fast text messages and emails are sent to their providers.

### Context and expected behaviour

A clinic launching a large recall campaign could place hundreds of simultaneous calls. That
overwhelms the voice provider, exhausts capacity other clinics need, and produces a wave of calls no
practice could handle the responses to.

Similarly, messaging and email providers enforce their own sending rates. Exceeding them gets messages
rejected or the account throttled.

Expected behaviour: a configurable ceiling on simultaneous outbound calls per clinic, and a sending
rate limit per provider. Work over the limit is **held and sent shortly after**, exactly as
quiet-hours work is held — never dropped.

### Current implementation status

**Not implemented.** There is no concurrency control for outbound calls, and no sending-rate control
for text or email.

Voice calling is otherwise well protected — a call is placed at most once, a call whose result never
arrives is cleaned up by a timeout, and a sweep recovers missed results. The volume ceiling is the
missing piece.

A thorough rate limiter already exists for the practice-software cloud connection and is the right
model to follow.

### Remaining work

- Add a configurable per-clinic ceiling on simultaneous outbound calls.
- Add per-provider sending rate limits for text and email.
- Hold work that exceeds either limit, with a retry time.
- Share all limits across running copies of the application.

### Watch out for

Counting calls in progress requires decrementing the count both when a call ends normally **and** when
a call times out without ever reporting back. Miss the timeout case and the count only ever rises,
until the clinic silently cannot place any calls at all and nothing explains why.

### Acceptance criteria

- A clinic cannot exceed its configured simultaneous call ceiling.
- The in-progress count returns to correct after a call that times out with no result.
- Text and email respect a configurable per-provider sending rate.
- Work over any limit is held and sent later, never dropped.
- Limits hold correctly with multiple copies of the application running.

---

## Item 19 · Voicemail handling options

### Description

When an outbound call reaches voicemail, the system always treats it the same way: no message is left,
and the attempt is counted as used. Neither behaviour is configurable, and both should be.

### Context and expected behaviour

Reaching voicemail is genuinely different from nobody answering, and clinics differ on how they want
it handled. Some want a message left. Some want a voicemail not to count against the retry allowance,
so the patient still gets the full number of live attempts.

Expected behaviour: two settings per campaign per clinic — whether to leave a message on voicemail,
and whether reaching voicemail consumes one of the configured attempts.

### Current implementation status

**Partially implemented.** Voicemail is already correctly recognised as its own outcome, distinct
from no-answer, and is reported that way. That half is done.

Neither setting exists. Today voicemail always consumes an attempt and no message is ever left.

### Remaining work

Add both settings to campaign configuration, expose them in the campaign builder, and apply them in
the calling step and in attempt counting.

### Watch out for

If voicemail does not consume an attempt, a number that always goes to voicemail could be dialled
indefinitely. Cap the total number of dials separately from the attempt count.

### Acceptance criteria

- Both settings are configurable per campaign per clinic.
- With "leave a message" enabled, a message is left; with it disabled, none is.
- With "does not consume an attempt" enabled, the patient still receives the configured number of live
  attempts.
- A number that always reaches voicemail cannot be dialled indefinitely.
- Voicemail remains distinct from no-answer in reporting.

---

## Item 20 · Quiet-hours exceptions

### Description

Quiet hours are derived entirely from each clinic's opening hours. There is no way to define an
exception — for a public holiday, for an individual patient, or for a particular kind of message.

### Context and expected behaviour

Quiet hours define when a clinic may contact patients. They currently come from the clinic's weekly
opening hours and correctly account for the clinic's own time zone and daylight saving.

Three kinds of exception are needed and cannot currently be expressed:

- **A specific date** — a public holiday when the clinic is closed but its weekly hours say open
- **A specific patient** — someone who has asked to be contacted only in the evenings
- **A kind of message** — a clinic may want a narrower window for marketing-type outreach than for a
  reminder about tomorrow's appointment

### Current implementation status

**Not implemented.** Quiet hours themselves work correctly, including time zones and daylight saving.
No exception mechanism of any kind exists.

### Remaining work

Add exception records at clinic and patient level, including date-specific entries, consulted ahead of
the default weekly hours. Expose them in the clinic's compliance settings.

### Watch out for

Two existing behaviours must be preserved exactly. A message that becomes due inside quiet hours is
**held until the window opens** — never dropped, never sent early. And a clinic with no permitted
window at all is blocked entirely rather than allowed through.

An exception that accidentally produces an empty window would block every message for that clinic.
Reject that when the exception is saved, not when a message fails to send.

### Acceptance criteria

- Clinic-level and patient-level exceptions are honoured ahead of the default weekly hours.
- A date-specific exception prevents contact on that date.
- An exception that would leave no permitted window is rejected when saved, with an explanation.
- Messages due during quiet hours are still held and sent when the window opens.
- A clinic with no configured window is still blocked.

---

## Item 21 · A store for inbound enquiries

### Description

The Sales Qualification campaign works on inbound enquiries rather than existing patients. There is
nowhere to put them.

### Context and expected behaviour

The other three campaigns act on people already in the practice's records. Sales Qualification starts
with someone who is not yet a patient — an enquiry from a web form or similar, with a name and a
contact detail and nothing else.

Expected behaviour: each enquiry is recorded when it arrives, with where it came from, what was
submitted, its qualification state as the conversation progresses, and which campaign run is handling
it. Submitting the same enquiry twice does not create two records.

### Current implementation status

**Not implemented.** No such store exists. This blocks Item 24 entirely.

### Remaining work

Create the enquiry store, scoped to a clinic and location like every other record in the system, with
the same access-isolation guarantees. Make it idempotent on an intake key so a resubmitted enquiry
does not duplicate.

### Watch out for

Enquiry contact details are personal information belonging to someone who is not yet a patient. Apply
the same encryption and data-minimisation treatment used for patient contact records.

### Acceptance criteria

- Enquiries are stored, scoped to one clinic and location.
- Data isolation between clinics is enforced and covered by an automated test, matching every other
  record type.
- Submitting the same enquiry twice creates one record, not two.
- Personal details are encrypted or minimised in line with patient contact records.

---

# Part 3 · The four campaigns

The Platform-side launch templates are built. The remaining Part 3 gap is running Overdue Recall
for GoTracker clinics only after history-sync completeness can be proven.

---

## Item 22 · Build out Appointment Reminder and Overdue Recall

### Description

The two Platform-side campaign templates that were only placeholders are now built to launchable
depth. This section is retained only as context for Item 23 and outcome reporting.

### Context and expected behaviour

Expected behaviour, per campaign:

**Appointment Reminder** — a short-notice nudge the day before or the day of the visit. It must run
completely independently of the Confirmation campaign: separately switched on, separately configured,
with its own runs. It is not a later stage of Confirmation. Because it fires close to the appointment,
it must re-check the appointment against the practice software immediately before sending — a
reminder for an appointment cancelled that morning is the most visible possible failure of this
product.

**Overdue Recall** — finds patients with no visit in a configured period and no future appointment,
and re-engages them to book. Unlike the others, its subject is a patient rather than an appointment,
and it runs as a periodic scan rather than in response to an event. Its goal is a booking, so it
depends on signed booking links. On NexHealth clinics its eligibility comes from the practice
software's own recall type, recall due date and treatment-plan context rather than guessing based on
last-visit dates.

### Current implementation status

**Implemented for the Platform launch-template path.** `appointment-reminder-24h` re-checks live
appointment state before every patient-directed send, configures confirm/reschedule links, waits for
SMS replies, confirms through `update_appointment`, and routes reschedule/cancel/staff asks to
staff-follow-up outcomes.

`recall-sms-6month` now uses Item 25 data through `recall_type_name`, `recall_due_date` and
`has_active_treatment_plan`. It excludes active treatment-plan patients from generic recall,
suppresses patients who already have a future appointment, applies the 90-day re-enrolment cooldown,
configures a signed booking link, handles booked/reschedule/staff replies, and records distinct
terminal outcomes.

### Remaining work

No remaining Platform template work for Item 22. GoTracker recall enrolment and history-sync refusal
remain Item 23.

### Watch out for

**GoTracker recall activation still waits on Item 23.** A broad recall scan against incomplete
history can tell recently seen patients they are overdue.

### Acceptance criteria

- Reminder is configured independently of Confirmation, with its own runs.
- A Reminder is never sent for an appointment that has been cancelled or moved, verified by test.
- Recall enrols from NexHealth recall records and signed booking links.
- Recall does not re-enrol a patient until the agreed 90-day cooldown has passed.
- Both templates handle no-answer, reply, opt-out and handoff paths with distinct recorded outcomes.
- GoTracker-specific recall activation is held for Item 23.

---

## Item 23 · Run Overdue Recall for GoTracker clinics

### Description

The recall scan is built around NexHealth clinics. It does not run for GoTracker clinics, and there is
no protection against it running on a clinic whose data has not fully synchronised.

### Context and expected behaviour

Recall works by finding patients who have not visited recently and have nothing booked. On NexHealth
clinics, the practice software provides its own recall information. GoTracker provides no equivalent,
so eligibility has to be worked out from the appointment history the Connector has synchronised.

That creates a risk unique to GoTracker: if the history sync has not finished, the platform's view of
who has visited recently is incomplete, and the scan will conclude that patients who were seen last
month are overdue. Contacting a patient to say "we haven't seen you in two years" three weeks after
their check-up is a serious, visible error.

Expected behaviour: recall runs on GoTracker clinics using synchronised appointment history, and
refuses to run for any clinic whose history synchronisation has not completed.

### Current implementation status

**Not implemented.** The recall enrolment path has no handling for GoTracker clinics, and no guard
against running on incompletely synchronised data.

### Remaining work

- Extend recall enrolment to work from synchronised appointment history for GoTracker clinics.
- Add a completeness check using the sync progress the Connector already reports, and refuse to run
  recall for a clinic that has not finished its history sync, recording why.

### Acceptance criteria

- Recall enrols correctly on a GoTracker clinic with a completed history sync.
- Recall refuses to run on a clinic whose history sync is incomplete, and the reason is recorded and
  visible.
- Patients with a recent visit in the synchronised history are never enrolled.

---

## Item 24 · Build the Sales Qualification campaign

### Description

The fourth contracted campaign does not exist in any form.

### Context and expected behaviour

This is the only campaign that starts from outside the practice. Someone enquires — through a web
form or similar — the AI agent contacts them, establishes whether they are a genuine prospective
patient and what they need, and books the qualified ones onto the right calendar.

The full flow:

1. **Intake** — the enquiry arrives and is recorded with its source.
2. **Engage** — the system contacts them on the configured channel; a call hands the conversation to
   the AI agent.
3. **Qualify** — the agent establishes intent. The outcome is qualified, not qualified, or
   unreachable.
4. **Match** — a qualified enquiry is matched to the right location, the right provider and the right
   appointment type. This is a genuine routing requirement, not a formality — booking a new patient
   with the wrong provider for the wrong treatment length disrupts the practice.
5. **Book** — an appointment is booked.
6. **Convert** — if this person is not already in the practice's records, a patient record is created.

### Current implementation status

**Not implemented**, in four separate respects: there is no store for enquiries (Item 21), no way for
an enquiry to start a campaign, no route for enquiries to arrive on, and no campaign template. It is
also blocked by Item 11, since qualification with no ability to book has no useful ending.

### Remaining work

- Build the intake route or webhook: authenticated, rate-limited, and idempotent so a resubmitted
  enquiry does not enrol the person twice.
- Add an enquiry-arrival trigger so an enquiry can start a campaign run.
- Build the campaign template: engage, qualify, branch on the outcome, match, book or hand to staff,
  and exit with a recorded result.
- Create a patient record on conversion where one does not already exist.

### Watch out for

- **Never create a duplicate patient.** If the enquirer is already in the practice's records, match
  them; do not create a second record. Both patient creation and booking must be safe to retry.
- **Do not book a poor-fit slot.** A qualified enquiry with no suitable appointment available within
  the acceptable window should be handed to staff, not squeezed into the nearest slot.
- On a GoTracker clinic the booking may come back as pending (Item 4). The enquiry's state must show
  "booked, awaiting confirmation from the practice" and be visible to staff as such.
- Which intake sources are in scope is an open decision — see **Decision C**. Connecting arbitrary
  third-party systems is explicitly out of scope, so the intake surface must stay within the agreed
  set.

### Acceptance criteria

- An enquiry submitted to the intake route starts exactly one campaign run; a resubmission starts
  none.
- The agent qualifies the enquiry and the outcome is recorded as qualified, not qualified, or
  unreachable.
- A qualified enquiry is matched to the correct location, provider and appointment type before
  booking.
- An enquirer already in the practice's records is matched, never duplicated.
- No suitable slot results in a handover to staff rather than a booking.
- The campaign records distinct outcomes for booked, not qualified, no response, and handed to staff.

---

# Part 4 · Expanded NexHealth integration — the six data families

The platform reads a limited set of information from NexHealth today: patients, providers,
appointments, appointment types, rooms, working hours, recall records, recall types, clinical-note
metadata, document metadata and treatment-plan metadata. The agreed scope covers a substantially
wider set, organised as **six named API families**. Patient Communication is now wired; Procedures,
Insurance and Financials remain the larger unbuilt families, and Working Hours still has
reconciliation work outstanding.

This table maps every contracted family to its item below, so nothing appears to be missing. The
family names are the ones used in the agreed scope — use them when discussing status with anyone
outside the dev team.

| Contracted API family | Status | Item |
|---|---|---|
| **Working Hours** | Built and in use. The reconciliation half is outstanding | Item 31 |
| **NexHealth Operations** | Built — sync statuses, webhook endpoints and subscriptions, staged v3 cutover. Only the onboarding interfaces remain | Item 29 |
| **Patient Communication** | Built for Item 25: clinical-note metadata, document types, patient-document metadata, patient recall records, recall types and treatment-plan metadata. Patient alerts are explicitly out by Decision G | **Item 25** |
| **Procedures** | **Barely started** — capped extract on a legacy path | **Item 26** |
| **Insurance** | **Not built** | **Item 27** |
| **Financials** | **Not built — the largest family in the scope** | **Item 28** |

Insurance and Financials are the two largest gaps in the whole engagement. Neither has a single line
of implementation anywhere in the platform.

## A requirement that applies to every item in Part 4

Each of these families brings sensitive information — clinical, financial or documentary — into the
platform. Every item below is only complete when all five of the following are true. This is not
optional hardening; an endpoint without it is not finished.

1. **Return only what is needed.** Each use returns the narrowest set of fields that satisfies it.
2. **Access control.** The caller's role must permit that class of information.
3. **Audit.** Every access is recorded.
4. **Explicit allow-lists.** A campaign or workflow declares which fields it may receive. Anything not
   declared is stripped out, not merely unused.
5. **Tests against the practice-software sandbox**, including checks that permissions are enforced and
   that sensitive fields are properly withheld.

Financial and clinical information must never reach the AI voice agent unless a specific workflow
explicitly requires it and declares it.

---

## Item 25 · Patient Communication API family — clinical notes, documents and recalls

### Description

A family of seven related record types covering clinical notes, documents and recall information.
The six supported record types are integrated; patient alerts are intentionally excluded by
Decision G.

### Context and expected behaviour

The seven types are: clinical notes, document types, patient documents, patient alerts, patient
recall records, recall types, and treatment plans.

Together they give the platform a real clinical picture of a patient rather than just their
appointment history. That matters most for the Recall campaign: a patient the practice software has
flagged as due for a specific recall type, or who is midway through a treatment plan, needs different
outreach from someone who simply has not been in for a while.

### Current implementation status

Implemented for the supported Item 25 surface. Patient recall records remain integrated and working,
including the fix to read the practice software's actual recall due-date field. The platform now also
reads clinical-note metadata, document types, patient-document metadata, recall types and
treatment-plan metadata through the PMS adapter layer.

Patient alerts are excluded by **Decision G**. The abandoned placeholder has been removed rather than
implemented because NexHealth's patient-alert read only covers alerts created through the NexHealth
API, not a complete view of staff-created PMS chart alerts.

### Why this is the highest-value item in Part 4

The Recall campaign's eligibility rules require the practice software's own recall information —
recall records, recall types, treatment plan context and visit history — rather than guessing from
last-visit dates. This family is now in place and is used by the Overdue Recall launch template from
Item 22.

### Implementation notes

- `GET /api/v1/pms/patients/{patient_id}/communication` returns a bounded,
  role-gated and audited communication snapshot.
- The snapshot deliberately omits clinical note bodies, document download URLs, treatment procedure
  details and fee-like fields.
- Workflows declare `pms_context_fields`; undeclared PMS-derived fields are stripped before trigger
  filters or run metadata are built.
- Recall audience rules can use `recall_due_date`, `recall_type_id`, `recall_type_name`,
  `recall_interval_months`, `last_visit_date`, `treatment_plan_statuses`,
  `active_treatment_plan_count` and `has_active_treatment_plan`.
- Publishing/readiness derives required PMS capabilities from those declared fields, including
  `patient_recalls`, `recall_types` and `treatment_plans`.

### Acceptance criteria

- The six supported record types can be retrieved for a patient; patient alerts remain excluded by
  Decision G with an explicit policy reason in the response.
- Recall types and treatment plans are usable in Recall's audience rules.
- The abandoned alerts placeholder is resolved by removal, with the decision recorded.
- All five Part 4 requirements are satisfied for each type, verified by test.

---

## Item 26 · Procedures API family — visit and treatment history

### Description

A patient's procedure history is available only as a small extract attached to their patient record,
limited to five entries, on an older interface version.

### Context and expected behaviour

Procedure history tells you what treatment a patient has actually had and when. It is the factual
basis for recall targeting and for follow-up after specific treatments.

Expected behaviour: procedure history can be requested directly for a patient, without an arbitrary
limit, on the current interface version, and is usable in campaign audience rules.

### Current implementation status

**Partially implemented, and inadequate for its purpose.** Procedures appear only as an embedded
extract inside the patient record, **capped at five entries**, retrieved over an older interface
version that the rest of the system has moved off. A patient with a long history returns five
arbitrary entries — unusable for deciding whether someone is due for care.

### Remaining work

Retrieve procedure history as its own request, uncapped, on the current interface version, and expose
it to campaign audience rules.

### Acceptance criteria

- Procedure history can be retrieved independently of the patient record.
- No arbitrary cap on the number of entries returned.
- The current interface version is used.
- Procedure history is usable in Recall audience rules.
- All five Part 4 requirements are satisfied.

---

## Item 27 · Insurance API family — plans and coverage

### Description

Insurance plans and coverage are currently held only as a local list maintained by hand. Where the
practice software holds authoritative insurance data, that should be the source.

### Context and expected behaviour

The AI voice agent answers patient questions about which insurance the practice accepts. Today it
reads a list typed in during clinic setup, which drifts out of date and is one more thing to maintain
per clinic.

Expected behaviour: insurance plans and coverage come from the practice software where it holds them,
so the agent's answers stay correct without anyone maintaining a list.

### Current implementation status

**Not implemented.** The insurance record in the platform is explicitly documented in code as locally
stored and not synchronised. Some coverage information appears as an incidental extract on the patient
record, but there is no insurance data family and nothing keeps it current.

### Remaining work

- Retrieve insurance plans and coverage from the practice software.
- Replace the local list, or clearly demote it to a fallback with the fallback condition enforced in
  code.
- Migrate existing clinics: some have local entries with no equivalent in the practice software.
- Route the voice agent's insurance answers through one path regardless of source.

### Watch out for

There is live local data in production, and clinic-entered answers are being used by the voice agent
right now. **The migration must not lose them.** Plan the migration path before writing code, and
decide what happens to a local entry with no practice-software equivalent.

Not every practice-management system behind NexHealth exposes insurance data, so the fallback path
must remain workable indefinitely for some clinics — see **Decision H**.

### Acceptance criteria

- Insurance plans and coverage are retrieved from the practice software where available.
- The local list is replaced, or demoted to a fallback whose condition is enforced in code.
- No clinic-entered insurance answer currently used by the voice agent is lost.
- The voice agent answers insurance questions correctly for both source types.
- All five Part 4 requirements are satisfied.

---

## Item 28 · Financials API family — charges, claims, payments and balances

### Description

The largest of the data families: charges, claims, payments, adjustments, balances, fee schedules and
payment plans. None of it is integrated.

### Context and expected behaviour

This family covers a patient's financial relationship with the practice: what they have been charged,
what insurance has been claimed and paid, what they owe, what payment plan they are on, and the
practice's fee schedule.

It supports billing-aware dashboards and carefully-guarded billing conversations — for example,
knowing not to send a cheerful recall message to someone in collections.

### Current implementation status

**Not implemented.** No part of this family exists anywhere in the platform.

### Remaining work

Integrate each record type in the family, with all five Part 4 requirements applied to every one.

### Watch out for

**This is the most sensitive information in the entire product.** The access controls and field
allow-lists are not polish to be added later — they are the reason this can be built at all. No
financial field may reach the AI voice agent unless a specific workflow explicitly declares that it
needs it. Build the allow-listing before the data retrieval, not after.

### Acceptance criteria

- Each record type in the family can be retrieved.
- No financial field reaches the voice agent without an explicit per-workflow declaration.
- Access control and audit are enforced on every access.
- Withholding of sensitive fields is proven by test.
- All five Part 4 requirements are satisfied.

---

## Item 29 · NexHealth Operations API family — onboarding interfaces

### Description

The practice-software provider offers interfaces for setting up a new clinic connection. Using them
would shorten clinic onboarding, which is currently manual.

### Context and expected behaviour

Bringing a new NexHealth clinic online involves a sequence of manual configuration steps. The provider
exposes interfaces that automate part of that.

### Current implementation status

**Not implemented.** The related monitoring and event-subscription interfaces from the same group
*are* built and working — this is the remaining piece.

### Remaining work

Integrate the onboarding interfaces and update the clinic onboarding runbook to show which manual
steps they replace.

### Acceptance criteria

- The onboarding interfaces are integrated and covered by tests against the provider's sandbox.
- The onboarding runbook reflects which manual steps are no longer needed.

---

## Item 30 · Block campaigns that need data a clinic cannot provide

### Description

Campaigns declare which practice-software capabilities they depend on. When a capability is
unavailable, the system currently warns and proceeds.

### Context and expected behaviour

Not every practice-management system exposes every kind of information. A campaign that depends on
treatment plans cannot work at a clinic whose software does not provide them.

Expected behaviour: if a campaign needs a capability the clinic cannot provide, it cannot be published
for that clinic. An unknown capability is treated as unavailable, not as available.

### Current implementation status

**Partially implemented.** The capability-checking mechanism exists and campaigns declare their
requirements. But where the underlying data family is not built at all — Items 26, 27, 28 — the
check cannot answer truthfully for those families.

### Remaining work

Make the check refuse rather than warn, and treat unknown as unavailable.

### Acceptance criteria

- A campaign requiring an unavailable capability cannot be published for that clinic.
- An unknown capability is treated as unavailable.
- The pre-launch checklist names the missing capability and explains why it blocks.

---

## Item 31 · Working Hours API family — reconcile clinic hours between the two sources

### Description

Clinic and provider working hours exist both in the practice software and in the platform's own
configuration. Nothing compares them, so they can disagree silently.

### Context and expected behaviour

The platform decides which appointment slots to offer patients based on working hours configured
during clinic setup. The practice software also holds working hours. If the practice changes their
hours in their own system and nobody updates the platform, the AI agent will keep offering slots the
clinic is closed for.

Expected behaviour: the two sources are compared, and any disagreement is **surfaced to an
administrator** — not silently resolved in either direction.

### Current implementation status

**Partially implemented.** Retrieving working hours from the practice software is built and used for
managing provider availability. The comparison against the platform's own configured hours does not
exist, and no disagreement is ever reported.

### Remaining work

Build a comparison that detects divergence per clinic location and reports it to an administrator, for
example in the pre-launch checklist and the scheduling screens.

### Watch out for

Do not automatically adopt either source. Slot offering depends on the configured hours, and silently
switching to the practice software's version would change which appointments the AI agent offers, on
live clinics, with no announcement. Surface the difference and let a person decide.

### Acceptance criteria

- Divergence between the two sources is detected and reported per location.
- Neither source silently overwrites the other.
- The disagreement is visible to an administrator before it can affect what the agent offers.

---

# Part 5 · Compliance, monitoring and operations

---

## Item 32 · Record who changed a campaign

### Description

Creating, editing, publishing, activating, deactivating or deleting a campaign leaves no audit record.

### Context and expected behaviour

The platform handles patient health information, and the operating requirement is that every
privileged action is recorded — who did it, when, and to what. This is met thoroughly across the
rest of the platform: patient lookups, bookings, cancellations, confirmations and administrative
changes are all audited, with a controlled list of recordable action types.

The campaign administration area — roughly forty-five separate operations — has **no audit coverage at
all**, and the controlled list of action types contains no campaign-related entries. Publishing a
campaign switches on automated contact with real patients. There is currently no record of who did it.

### Current implementation status

**Not implemented.** Zero audit coverage on the campaign administration surface.

### Remaining work

- Add campaign-related action types to the controlled list.
- Record an audit entry for every operation that changes campaign state, capturing who acted, which
  clinic, which campaign and version, and enough detail to reconstruct the change.
- Make publishing and activating separately identifiable in the audit trail.
- Audit compliance-setting changes specifically.

### Watch out for

Campaign definitions contain message wording and placeholders, not patient data — but a test run or an
audience preview can contain patient information. Record the action and the identifiers, never the
full payload.

### Acceptance criteria

- Every campaign operation that changes state produces an audit record.
- Action types come from the controlled list; no free-text action names.
- Publishing and activating are distinguishable in the audit trail.
- Compliance-setting changes are audited.
- An automated test confirms no state-changing campaign operation is left unaudited.

---

## Item 33 · Permissions for high-consequence actions

### Description

Four specific privileged actions have no permission controlling them: viewing a clinic's sync status,
replaying a failed write, resolving a conflict, and changing campaign configuration.

### Context and expected behaviour

Replaying a write and resolving a conflict both write into a live practice's schedule. They are the
highest-consequence actions in the product and should require more than ordinary access.

Expected behaviour: each of the four is governed by its own permission, and a user without it is
refused.

### Current implementation status

**Not implemented.** Access control today restricts users to their own clinic and locations, which is
a different control and works correctly. None of the four named permissions exists. Campaign
configuration is reachable by any role that passes the location restriction, and there is no
permission gating write replay or conflict resolution — the latter arriving with Item 2.

### Remaining work

Add the four permissions, enforce them on the relevant operations, and extend the automated
permission-coverage test to include them.

### Watch out for

Gate replay and conflict resolution **above** ordinary campaign editing. Someone who can edit message
wording should not automatically be able to force a write into a practice's schedule.

### Acceptance criteria

- Each of the four permissions exists and is enforced.
- A user without a permission is refused, and this is covered by the automated permission test.
- Each of the four actions is also audited, per Item 32.

---

## Item 34 · Record what caused each write to a practice's records

### Description

When the platform writes into a clinic's practice software, nothing records which campaign, which
step, or which patient interaction caused it.

### Context and expected behaviour

Every write into a clinic's records must be traceable to its cause. When a practice asks "why did this
appointment get changed?", or when a duplicate booking needs investigating, someone must be able to
trace it back to the specific campaign run and patient conversation that produced it.

The platform already generates a tracing identifier that follows a patient interaction through the
system. It does not currently reach the queued write.

### Current implementation status

**Not implemented on the write path.** Queued and replayed writes carry no originating campaign,
step, actor, or tracing identifier, in either the Platform or the Cloud Service.

### Remaining work

Record the originating campaign run, step and actor on each queued write, and carry the tracing
identifier through to the Cloud Service's record of it.

### Acceptance criteria

- A queued write records which campaign run, step and actor caused it.
- The tracing identifier survives from the Platform through to the Cloud Service record.
- An operator investigating a duplicate or unexpected booking can trace it to the campaign run
  responsible, without reading source code.

---

## Item 35 · Alert on campaign engine problems

### Description

The campaign engine publishes its own health figures every minute. Nothing watches them.

### Context and expected behaviour

The engine reports how much work is overdue, how much is stuck, how many campaign runs are active,
and how many runs and steps have failed. If overdue work climbs or failures spike, patients are not
being contacted and nobody knows.

### Current implementation status

**Partially implemented — measurement without alerting.** The figures are collected and published
every minute, and a queue equivalent does the same. **No alarm consumes any of them.**

Alerting infrastructure exists and works: there is an alert channel with email delivery and four
alarms wired to it, all watching infrastructure — a failure to record audit data, and the database
server's processor, storage and error rate. None watches the campaign engine.

### Remaining work

Add alarms on overdue work, stuck work, failed runs, failed steps, queue depth and undeliverable
items, routed to the existing alert channel. Add an alarm for the service cut-off state from Item 17.

### Watch out for

Choose thresholds from observed values in staging rather than guessing, or the first week produces
nothing but false alarms and the alerts get muted.

### Acceptance criteria

- Alarms exist for overdue work, stuck work, failed runs, failed steps, queue depth and undeliverable
  items.
- Each is routed to the existing alert channel and has been confirmed to fire in staging.
- Every threshold is documented with the reasoning behind the number.

---

## Item 36 · A screen for messages that could not be delivered

### Description

Work that fails permanently is recorded, but operators have no way to see or act on it.

### Context and expected behaviour

When something fails repeatedly and cannot be retried further, it is set aside for a human. Without a
screen, nobody knows those items exist. A patient who should have been contacted simply never was, and
nothing surfaces it.

Expected behaviour: an operator can see the set-aside items for their clinic, understand why each
failed, and either retry it or dismiss it with a recorded reason.

### Current implementation status

**Partially implemented — backend only.** Failed items are captured and stored, and there is a way to
retrieve them. **There is no screen in the dashboard.**

### Remaining work

Build the operator screen listing set-aside items with the failure reason and the originating campaign
run, with retry and dismiss actions.

### Watch out for

Retry is a privileged action (Item 33) and must be audited (Item 32). It must also be safe against
double-clicking — retrying twice must not contact the patient twice.

### Acceptance criteria

- Operators see only their own clinic's items.
- Each item shows the failure reason and the campaign run it came from.
- Retry is available, permission-gated, audited, and safe to trigger twice.
- An item can be dismissed with a recorded reason.

---

## Item 37 · Outcome reporting: recalls booked, enquiries qualified, revenue attributed

### Description

Three of the reporting figures in scope are not produced: how many recalls resulted in a booking, how
many enquiries were qualified, and how much revenue is attributable to campaigns.

### Context and expected behaviour

These three are the figures that answer "is this working?" Message counts show activity; these show
outcomes.

### Current implementation status

**Partially implemented.** Campaign reporting is real and working — enrolments, active runs,
completions, failures, cancellations and suppressions, with daily roll-ups. Usage and cost tracking
per clinic and per campaign also works, covering message volumes, call minutes and spend.

The three outcome figures above are not produced. Two have dependencies: recalls booked needs the
booking step (Item 11), and enquiries qualified needs the Sales Qualification campaign (Item 24).

### Remaining work

Produce all three figures per campaign per clinic, include them in the daily roll-up, and display them
in the campaign reporting screen.

### Watch out for

Revenue attribution needs a rule that will survive a client challenging it. Per-appointment-type values
can be configured per clinic. Whatever rule you adopt, **display it alongside the figure** — an
unexplained revenue number invites an argument rather than settling one. The rule itself is
**Decision A**.

### Acceptance criteria

- All three figures are reported per campaign per clinic.
- The attribution rule is displayed alongside the revenue figure.
- All three are included in the daily roll-up.
- The figures reconcile against the underlying campaign runs.

---

## Item 38 · Disaster recovery procedures

### Description

There are no written or rehearsed procedures for recovering the platform or the Cloud Service after a
serious failure.

### Context and expected behaviour

The system holds patient health information and is mid-conversation with real patients at any moment.
Recovery must be a written procedure someone can follow under pressure, and it must have been
rehearsed at least once, because a procedure that has never been run is a guess.

Recovery covers three things: the database, the queued work in both the platform and the Cloud
Service, and campaign runs that were in progress.

### Current implementation status

**Not implemented.** No recovery documentation exists for either system.

### Remaining work

- Write procedures for database restore, queue recovery, and resuming in-progress campaign runs, for
  both the Platform and the Cloud Service.
- State the recovery point and recovery time objectives, and confirm the actual backup configuration
  supports them.
- Rehearse against staging and record the result.

### Watch out for

In-progress campaign runs are the difficult part, not the database. Restoring to an earlier point can
revive scheduled work that has already run, and re-contact patients who were already contacted.
Recovery must be safe against that. The same applies to the queued writes in the Cloud Service — a
restore must not re-write appointments that already reached the practice, which is Item 3 again.

### Acceptance criteria

- Written procedures exist for database restore, queue recovery and run resumption, for both systems.
- Recovery point and time objectives are stated and supported by the real backup configuration.
- The procedure has been rehearsed against staging and the outcome recorded.
- Recovery re-contacts no patient and re-writes no appointment.

---

## Item 39 · Privacy and audit review

### Description

A deliberate review confirming that no patient information leaks into logs anywhere, and that every
privileged action is recorded.

### Context and expected behaviour

Patient information must not appear in log output at any layer — including on the clinic's own machine,
where raw database query contents must never be written to a log file. And every privileged action
must be recorded.

This is a review pass with a written outcome, not a feature.

### Current implementation status

**Not performed, and it would not pass today.** Campaign administration has no audit coverage
(Item 32), and writes carry no originating actor (Item 34). The review should follow those items
rather than precede them.

### Remaining work

After Items 32, 33 and 34 are complete, review log output and audit coverage across the Platform, the
Cloud Service and the Connector. Record findings and fix them.

### Acceptance criteria

- Every privileged action across all three systems produces an audit record; any gaps are listed and
  closed.
- Sampled log output from all three contains no patient information.
- The Connector's logs contain no raw database query contents.
- Findings and their remediation are written down.

---

## Item 40 · Respond to penetration-test findings

### Description

Remediation of findings from the security penetration test.

### Context and expected behaviour

An independent security test is planned. Its findings need to be tracked, prioritised and either fixed
or formally accepted.

### Current implementation status

**Not started.** No record of a test or of any findings.

### Remaining work

Agree how findings will be received and tracked before the test runs, then remediate.

### Acceptance criteria

- Findings are tracked with a severity and an owner.
- Each is either fixed or explicitly accepted with a written justification.

---

# Part 6 · Testing, delivery and documentation

---

## Item 41 · Test the practice-database write path properly

### Description

The Connector writes into a real clinic's database using the vendor's supported procedures. There is
no test environment for that, no coverage of the write procedures, and no tests for the failure modes
that matter.

### Context and expected behaviour

Three kinds of coverage are needed:

- **A sandbox** — a practice database populated with realistic test data that the test suite runs
  against, so writes can be verified without touching a clinic.
- **Write procedure coverage** — the vendor supplies twelve supported write procedures. Each needs
  exercising, because these are the only sanctioned way to change a clinic's data.
- **Failure-mode tests** — four specific scenarios: the Connector is offline, the clinic's database is
  unavailable, the same write is attempted twice, and a booking is written against an appointment that
  has since changed.

### Current implementation status

**Partially implemented, unevenly.** The Cloud Service has good self-test coverage of its own logic —
booking rules, data handling, the slot engine, message delivery. The Connector has automated tests for
its watchdog and restart behaviour.

Missing entirely: any sandbox practice database, any coverage of the twelve write procedures, and any
of the four failure-mode tests.

### Why this matters more than it looks

The four failure modes are precisely the risks that Items 1, 2 and 3 exist to close. Duplicate write
and stale appointment are not hypothetical scenarios here — they are the exact behaviours those items
introduce protection against, and these tests are the only way to prove the protection works.

### Remaining work

- Stand up a practice-database sandbox with seeded data for automated tests.
- Exercise each of the twelve supported write procedures.
- Write the four failure-mode tests, each proving the corresponding protection from Items 1 to 3
  holds.

### Acceptance criteria

- A seeded sandbox database exists and the suite runs against it.
- All twelve write procedures are exercised by tests.
- All four failure modes are covered.
- Killing the Connector between writing and acknowledging is an actual automated test, not a thought
  experiment, and proves no duplicate appointment is created.

---

## Item 42 · End-to-end test of the offline booking path

### Description

No test covers a booking travelling the full path from the platform to the clinic's database and back.

### Context and expected behaviour

The individual pieces are tested in isolation on both sides. Nothing exercises the whole journey:
platform requests a booking, Cloud Service accepts and queues it, Connector pulls it, writes it,
acknowledges it, and the platform learns the real appointment number.

That whole journey is the product's most distinctive capability and its highest-risk path.

### Current implementation status

**Not implemented.**

### Remaining work

Build end-to-end coverage of the full path, including the offline and conflict variations.

### Acceptance criteria

- A booking made while the Connector is offline is accepted, queued, written on reconnection, and
  reported back with the real appointment number.
- The same path, with the slot taken in the meantime, produces a conflict and no write.
- Cancellation and confirmation complete the full round trip.
- Patient lookup and slot search are covered on both kinds of clinic.

---

## Item 43 · Test each new data family against the provider sandbox

### Description

Each newly integrated data family needs tests against the practice-software provider's sandbox,
including permission enforcement and withholding of sensitive fields.

### Current implementation status

Item 25 added its coverage with the Patient Communication family. The same coverage **cannot exist
yet** for the three unbuilt families (Items 26, 27, 28). Build the tests alongside each family rather
than afterwards — they are part of that family's definition of done.

### Acceptance criteria

- Each data family has sandbox test coverage.
- Each includes a check that permissions are enforced.
- Each includes a check that sensitive fields are withheld from callers not entitled to them.

---

## Item 44 · Prove clinic data isolation for the new data families

### Description

Automated tests proving that one clinic can never read or write another clinic's data must extend to
every newly added data family.

### Context and expected behaviour

Clinic separation is enforced at the database level and proven by automated tests today. Every new
kind of data must be covered by the same guarantee — a cross-clinic read or write is the most serious
class of defect in this product.

### Current implementation status

**Partially implemented.** Campaign data, runs, attempts, consent, suppression and reporting are all
covered, and there is an additional automated check that catches queries missing a clinic restriction.

Coverage cannot extend to four of the new data families because they do not exist yet.

### Remaining work

Extend the isolation test suite as each new family lands.

### Acceptance criteria

- Every new data family is represented in the isolation test suite.
- An attempted cross-clinic read or write on any new data type fails the suite.

---

## Item 45 · Load testing

### Description

The campaign scheduler and the queue workers have never been tested at volume. Their limits are
unknown.

### Context and expected behaviour

You need to know how many campaign steps per minute the system can process, how deep a queue it can
work through, and — most importantly — what happens when those limits are exceeded. The acceptable
answer is that work backs up and is processed late. The unacceptable answers are lost work or
duplicate contact.

### Current implementation status

**Not implemented.** Unit and integration coverage is strong and there is a load-testing area in the
codebase, but nothing covers campaign scheduling or queue processing at volume.

### Remaining work

Load-test campaign scheduling and queue processing, and document the throughput ceiling and the
behaviour at that ceiling.

### Watch out for

The most valuable scenario is a recall scan across a large patient list, which must run in batches,
survive interruption, and not overwhelm the practice software. It now has a second consumer to
consider: the queue of writes waiting to reach clinics.

### Acceptance criteria

- Scheduler and queue throughput are measured and documented.
- At the ceiling, work backs up and is processed late — no work is lost and no patient is contacted
  twice.
- A full-population recall scan is proven to run in batches, survive interruption, and stay within
  rate limits.
- The measured values inform the alarm thresholds in Items 6 and 35.

---

## Item 46 · Automated build, migration checks and deployment

### Description

There is no automated build or deployment pipeline in any of the three systems. Everything is run by
hand.

### Context and expected behaviour

Deploying currently means running scripts manually. Nothing automatically runs the tests before a
change is merged, nothing verifies database migrations are consistent, and nothing confirms a
deployment actually worked.

Expected behaviour: on a proposed change, the tests and checks run automatically and a failure blocks
the merge. On merge, the system builds, applies database migrations, deploys, and runs a basic check
that it is working.

### Current implementation status

**Not implemented in any of the three systems.** Deployment tooling exists as manually-run scripts,
plus infrastructure definitions. There are no automated checks and no deployment smoke test.

### Remaining work

- On a proposed change: run linting, the unit tests and the integration tests, plus a check that the
  database migration history has not forked into two conflicting branches.
- On merge: build, migrate, deploy, and smoke-test.
- For the Connector, include code-signing of the installer in the release process.

### Watch out for

The migration history has forked more than once already, which is why that check belongs on the
proposed-change job rather than at deployment time — by then it is too late.

A failed migration must stop the deployment, not leave the system half-migrated.

### Acceptance criteria

- Proposed changes automatically run linting and both test suites, and fail on a forked migration
  history.
- Merging triggers build, migration, deployment and a smoke test with no manual step.
- A failed migration halts the deployment rather than leaving it partly applied.
- The Connector installer is code-signed as part of its release process.

---

## Item 47 · Guides for clinic administrators

### Description

Documentation written for the people running a clinic, rather than for engineers or operators.

### Context and expected behaviour

Three guides are needed:

- **Campaign setup** — how a clinic administrator switches a campaign on, sets its timing, chooses
  channels, edits the wording and sets retry rules
- **Clinic configuration** — opening hours, breaks, appointment buffers, appointment types, transfer
  numbers and insurance answers
- **Connection health** — how to read the connection status screen and what to do about what it shows

### Current implementation status

**Not implemented.** Technical documentation across all three systems is thorough. Clinic-facing
guides do not exist.

Note that a separate onboarding process pack already exists covering how *your operators* take a
clinic live, and it is detailed. The gap is guidance for the clinic's own administrator using the
product day to day — campaign setup above all.

### Remaining work

Write the three guides, in plain language for a non-technical reader, cross-referencing the existing
operator onboarding pack rather than duplicating it.

### Acceptance criteria

- All three guides exist and are written for a non-technical clinic administrator.
- The campaign setup guide covers switching on, timing, channels, wording and retry rules.
- The connection health guide matches the screen delivered in Item 7.
- None duplicates the existing operator onboarding pack.

---

# Part 7 · Decisions needed before some items can start

These are product decisions, not technical ones. **Do not resolve them by assumption** — each changes
what gets built. Raise them early; several block work in the first stages.

| # | Decision needed | Blocks | Who decides |
|---|---|---|---|
| **A** | How is campaign-attributed revenue calculated? Per-appointment-type values can be configured per clinic, but the rule must be agreed and displayed. | Item 37 | Internal, then confirm with client |
| **B** | When a booking is accepted but not yet written to a GoTracker practice, should the campaign pause and wait for confirmation, or finish and hand the patient to staff? This changes the campaign design and what the patient is told. | Items 4, 11 | Client |
| **C** | Which sources may submit sales enquiries? Connecting arbitrary third-party systems is out of scope, so the permitted list must be agreed before the intake route is built. | Item 24 | Client |
| **E** | When a patient declines a confirmation, should anything happen automatically beyond recording it — cancel the appointment, create a staff task, offer a rebooking? | Campaign 1 completeness | Client |
| **G** | Patient alerts were started and abandoned. In or out? | Item 25 | Internal |
| **H** | For clinics whose practice software does not expose insurance data, does the manually-maintained local list remain the permanent source? | Item 27 | Internal |
| **I** | For the Connector: keep a durable local record of in-flight writes, or rely on the write identifier alone? Pick one and record why. | Item 5 | Internal |

**Already resolved — no action needed.** The limit on how often a single patient may be contacted
across all campaigns is **built and working**: a cap of three contacts per rolling seven days,
counted across every campaign for that clinic, not per campaign. Decision D is answered for Item 22
as a 90-day recall re-enrolment cooldown. Decision F is answered for generic recall by excluding
patients with an active treatment plan rather than changing their copy.

---

# Part 8 · What is already built — do not rebuild

Verified working. Some of this appears as outstanding in older task lists; those lists are out of
date. **Do not spend time here.**

**The GoTracker path.** The queue of writes waiting to reach a clinic, with attempt counting and
error recording. Acceptance of bookings while a clinic is offline, validated against working hours
and existing appointments in a single transaction so two simultaneous bookings cannot collide. The
full pull-and-acknowledge cycle for bookings, status changes, comments and patient records. Bounded
retry with automatic re-queueing of temporary failures. Operator re-queueing of a failed write.
Holding a booking whose patient does not yet exist in the practice software rather than failing it.
Data reconciliation with protection against a partial read causing a mass deletion. Identity mapping
between systems. Outbound event delivery with signing and replay. Slot generation. Working hours and
overrides. The Connector's Windows service, watchdog, self-update, installer, credential storage in
Windows-protected storage, and heartbeat reporting. The Cloud Service's operator interface, audit
trail, per-clinic rate limiting, permission scopes, and infrastructure monitoring.

**The campaign engine.** Scheduled work leased to workers so a failure is recovered rather than lost
or run twice. Protection against enrolling the same patient twice for the same event, and against
sending the same message twice. Campaigns pinned to the version they started under, so an edit does
not change a conversation already underway. The compliance gate, in the correct order: emergency stop,
then quiet hours, then contact details, then do-not-contact, then consent appropriate to the message
type. Quiet hours with correct time zone and daylight-saving handling. An emergency stop that halts
all outbound activity instantly. Re-checking a patient's situation immediately before every send,
including on GoTracker clinics. Clinic data isolation enforced at the database level and proven by
tests. A cap on how often a patient is contacted across all campaigns. Exclusion of patients who
already have a future appointment from recall. A guard against contacting someone after their
appointment has started. Text replies correctly matched to a single campaign run, so one reply can no
longer confirm several appointments. A controlled vocabulary of call outcomes. Confirmation
write-back on NexHealth. A write-back step that works on both kinds of clinic. Text and email replies
resuming a campaign. Retry with backoff on email, with permanent failures correctly not retried.

**Practice-software integration.** Working hours. Monitoring and event subscriptions, including a
staged migration to the provider's newer interface with no missed events. Patient recall records.
Per-clinic credentials for practice software, telephony and email, so each clinic runs on its own
accounts. Patient search and patient creation on GoTracker clinics. Appointment data projection.

**Two complete campaigns.** A pre-appointment confirmation campaign and a post-visit follow-up
campaign, live and running on both kinds of clinic, each handling repeat attempts, callbacks,
reschedules, cancellations, opt-outs and unreachable patients.

**Supporting capability.** Usage and cost tracking per clinic and per campaign. Live campaign progress
reporting. Tracing identifiers across a patient interaction. Recording and transcript retention rules.
Transfer numbers per location. Message placeholders with a closed catalogue that fails a send rather
than emitting a blank. Credential rotation documentation.

---

# Part 9 · Build order

Work top to bottom. The order reflects dependencies and risk — the first group prevents active harm
to clinics and patients.

### Stage 1 · Make the booking write path safe
Nothing here adds features. All of it removes ways the software can currently damage a clinic's
schedule or mislead a patient, with no error to warn anyone.

**Item 3** (prevent duplicate writes) → **Item 1** (check before writing) → **Item 2** (conflict
outcome) → **Item 4** (report pending honestly) → **Item 5** (restart recovery decision)

*Done when:* a booking made while a clinic is offline is written exactly once, or refused with a
reason a human can act on — and no patient is ever told "confirmed" before it is true.

### Stage 2 · Close the remaining silent failures
**Item 12** (generate links) · **Item 32** (campaign audit) · **Item 14** (text retry) ·
**Item 15** (delivery results) · **Item 6** (connection alerts) · **Item 35** (engine alerts)

### Stage 3 · Let campaigns finish what they start
**Item 11** (booking step) · **Item 30** (block unsupported campaigns) · **Item 16** (cross-channel
suppression) · **Item 13** (enforce readiness at publish)

### Stage 4 · Complete the four campaigns
**Item 23** (Recall on GoTracker) · *then, last,* switch GoTracker recall on for clinics once the
history-sync guard is in place

### Stage 5 · Practice-software data coverage
Independent of Stages 2–4 and can run in parallel with a second developer. This order matters — the
earlier families unblock recall quality and the capability check.

**Item 25** (Patient Communication) · **Item 26** (Procedures) · **Item 27** (Insurance) ·
**Item 28** (Financials) · **Item 29** (onboarding interfaces) · **Item 31** (Working Hours reconciliation) ·
**Items 43 and 44** as each family lands

### Stage 6 · Operator tools and resilience
**Item 7** (health screen) · **Item 8** (mapping review) · **Item 36** (undeliverable screen) ·
**Item 33** (permissions) · **Item 34** (write provenance) · **Item 17** (stop calling failing
services) · **Item 18** (volume limits) · **Item 20** (quiet-hours exceptions) · **Item 19**
(voicemail options) · **Item 9** (signed Connector messages)

### Stage 7 · Proof, operations and documentation
**Item 41** (practice-database tests) · **Item 42** (end-to-end booking) · **Item 45** (load testing)
· **Item 46** (automated build and deployment) · **Item 10** (GoTracker runbooks) · **Item 47**
(clinic guides) · **Item 37** (outcome reporting) · **Item 38** (disaster recovery) · **Item 39**
(privacy and audit review) · **Item 40** (penetration-test response)

---

*Every item in this document was verified against the running code before being included. Anything
already built has been removed.*
