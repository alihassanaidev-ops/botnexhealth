# Scope Decisions — Log

Status of the nine product decisions in Part 7 of [OUTSTANDING_SCOPE.md](OUTSTANDING_SCOPE.md).
Each entry records the answer, who gave it, and what it changes in the build. Decisions that are
still open name who they are waiting on.

Last updated 2026-08-30.

| # | Subject | Status | Blocks |
|---|---|---|---|
| A | Revenue attribution rule | **Deferred** | Item 37 (partially — see below) |
| B | Pending GoTracker booking: pause or hand off | **Open · client · urgent** | Items 4, 11 |
| C | Permitted sales-enquiry sources | **Decided** | Item 24 |
| D | Recall re-enrolment cooldown | **Decided** | Item 22 |
| E | What happens when a patient declines | **Decided** | Campaign 1 completeness |
| F | Treatment-plan status and recall | **Decided** | Items 22, 25 |
| G | Patient alerts: in or out | **Open · internal** | Item 25 |
| H | Insurance fallback permanence | **Open · internal** | Item 27 |
| I | Connector in-flight write recovery | **Open · internal** | Item 5 |

---

## D · Recall re-enrolment cooldown — DECIDED

**Answer.** 90 days, **configurable per clinic** rather than a fixed constant.

**Consequence.** A cooldown setting on the Overdue Recall campaign configuration, default 90 days,
read by the recall scan's eligibility query. This fits an existing pattern rather than inventing
one — recall templates already carry trigger config (`recall_interval_months`, currently 6 and 18)
in `src/app/services/automation/campaign_templates.py:404`, and per-campaign cooldown fields already
exist (`patient_voice_cooldown_hours`, same file, line 165). Build it the same way and expose it in
the campaign builder.

Keep this distinct from two things it is easily confused with: `recall_interval_months` is *how long
since the last visit makes someone eligible*, and the built-and-working cap of three contacts per
rolling seven days is a *rate limit across all campaigns*. This decision is only about how long an
unresponsive patient waits before a fresh recall cycle.

**Still open (minor).** Whether a lifetime cap on recall cycles exists — after N cycles with no
response, mark the patient unreachable and stop. Recommend building it as a second setting with a
default of 3 so the answer can change without a code change.

---

## E · What happens when a patient declines a confirmation — DECIDED

**Answer.** Do **not** auto-cancel the appointment. Record the decline, create a task for the front
desk, and optionally offer a rebooking in the same conversation.

**Why it matters.** Auto-cancelling means writing into a live practice schedule based on
interpreting a voice or SMS reply. A single misread reply cancels a real patient's appointment,
and the clinic finds out when the patient arrives.

**Consequence — this implies new work.** There is **no staff-task concept in the platform today**.
`src/app/models/` has `notification.py`, `external_notification_recipient.py` and
`user_email_notification_preference.py`, but no task or to-do record. So "create a staff task" is
either a light task record with an assignee and a resolved state, or it rides on the existing
notification system. Decide which before Item 22 starts; the notification route is cheaper and
probably sufficient if staff only need to be told, not to track completion.

---

## F · Treatment-plan status and recall — DECIDED

**Answer.** Patients in active treatment are **excluded** from generic Overdue Recall. A
"we haven't seen you in a while" message is wrong for someone already mid-plan.

**Consequence.** Treatment-plan status becomes an **audience filter**, not message personalisation.
That makes **Item 25 a hard dependency of Item 22** rather than a quality improvement to it — Recall
cannot be switched on correctly until treatment-plan data is being retrieved. This strengthens the
existing sequencing advice to build the Patient Communication family first.

---

## C · Permitted sales-enquiry sources — DECIDED

**Answer.** A signed webhook. Connecting arbitrary third-party systems stays out of scope.

**Consequence.** Item 24's intake surface is one authenticated, rate-limited, idempotent endpoint
that the clinic's own site (or a form provider they control) posts to. No per-source adapters, no
OAuth flows to third-party CRMs. Simplifies the item considerably.

**Also decided.** Staff can enter an enquiry by hand. It goes through the same intake path a form
does rather than inserting a row directly, so deduplication and consent recording behave identically
whichever way somebody arrived — two paths writing one table by different rules is how one of them
ends up wrong. Consent is asked for explicitly and never implied by the act of saving the form.

---

## A · Revenue attribution rule — DEFERRED

**Status.** Set aside for now, by decision.

**Consequence — Item 37 is only partially blocked.** It asks for three outcome figures, and only
one of them needs this rule:

- **Recalls booked** — needs Item 11, not Decision A
- **Enquiries qualified** — needs Item 24, not Decision A
- **Revenue attributed** — needs Decision A

Build and ship the first two with the rest of Item 37; leave the revenue figure out of the screen
entirely rather than displaying an unexplained number. When the rule is agreed, the doc's own
requirement stands: **display the rule alongside the figure.**

---

## B · Pending GoTracker booking — OPEN · CLIENT · URGENT

The one to chase. When a booking is accepted for a GoTracker clinic but not yet written to the
practice software, does the campaign pause and wait for confirmation, or exit and hand the patient
to staff? It changes campaign design and what the patient is told, and it gates **Item 4** (first
stage of the build order) and **Item 11** (third stage).

**Recommendation to put to the client:** exit the campaign with a pending outcome, but send the
patient their confirmation off the write-back event rather than holding the run open. Short runs,
loop still closed with the patient.

The Item 4 plumbing — a write-status field and a `pending` outcome distinct from success and
failure — can be built either way, so this does not block starting.

---

## G · Patient alerts — OPEN · INTERNAL

**Recommendation: out.** `PatientAlert` at `src/app/api/models.py:391` is `id`, `patient_id`,
`note`, `disabled_at` — a **free-text note**, and the field on `Patient` is commented out at line
443. Free text cannot safely gate outreach; you cannot reliably parse "do not call" out of a note,
and it is clinical content. Remove the placeholder and record why. If staff want alerts visible,
that is a read-only dashboard field later, not an automation input.

---

## H · Insurance fallback permanence — OPEN · INTERNAL

**Recommendation: permanent fallback**, because not every practice-management system behind
NexHealth exposes insurance data. Enforce the fallback condition in code rather than documenting it,
and surface a "last reviewed" date so a list nobody has maintained is visibly stale rather than
silently wrong. The voice agent reads this data live today, so Item 27's migration must not lose any
clinic-entered answer.

---

## I · Connector in-flight write recovery — OPEN · INTERNAL

**Recommendation: rely on Item 3's write identifier**, not a durable local record. It keeps patient
data off the clinic's Windows machine entirely, and if Item 3 is built properly the identifier
already sits in the practice database where a restarted Connector can look it up. A local store
reintroduces encrypted PHI at rest on a machine we do not control. A small non-PHI marker locally is
fine; patient data is not.

Whichever way it goes, the scope doc requires the decision and its reasoning to be written down.
