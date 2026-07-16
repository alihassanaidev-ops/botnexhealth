# Campaign Workflow Builder PM Deep Dive

Date: 2026-07-16  
Audience: Product, engineering, design, leadership  
Scope: Dental campaign/workflow builder, campaign management, and outbound engagement workflows

## Executive Summary

The current workflow builder is a solid technical MVP for outbound dental communication, but it is still closer to a generic automation editor than a dental growth and patient-retention product. It can create workflows from a small set of templates, place SMS/email/voice/wait/condition/exit nodes on a canvas, validate definitions, publish versions, run dry-runs, pause/archive campaigns, and enroll contacts. That foundation is valuable.

The main product gap is that the experience does not yet help a dental office answer the real operating questions:

- Who should we contact today?
- What appointment, provider, procedure, insurance, or recall context should the message include?
- Did the patient confirm, book, cancel, ask for a callback, or need staff follow-up?
- How much production/revenue did this campaign recover?
- Which campaigns are safe to run, compliant, and not annoying patients?

The recommendation is to evolve this from a basic workflow canvas into a dental campaign operating system: guided campaign setup, dental-specific triggers and segments, richer merge fields, patient response handling, campaign analytics, consent/preference management, and DSO/multi-location rollout controls.

## Current State Observed

### What Exists

The platform currently has:

- A React Flow visual builder with drag-and-drop workflow nodes.
- Workflow types for `wait`, `send_sms`, `send_voice`, `send_email`, `condition`, and `exit`.
- Trigger types for appointment offset, recall scan, manual enrollment, and bulk import.
- Backend support for a `callback_requested` trigger, although the frontend type/catalog does not fully expose it yet.
- Published immutable workflow versions.
- Local browser autosave for unsaved builder changes.
- Validation panel with client and server validation.
- Compliance metadata for content class and consent requirement.
- Channel readiness warnings for SMS, email, and voice.
- Test/dry-run simulation.
- Campaign lifecycle controls: pause, resume, archive, emergency halt.
- Basic run APIs and manual/bulk enrollment APIs.
- Four campaign templates:
  - Appointment Reminder 24h
  - Appointment Confirmation 48h
  - Recall Outreach 6-month
  - Reactivation Campaign 18-month
- Per-campaign usage/cost reporting exists at API level through workflow-tagged usage events.

### What Is Still Basic

The current campaign list mostly shows name, status, and trigger. The builder gives technical authoring power, but it does not yet provide enough dental workflow intelligence, campaign performance visibility, or operator guidance. Merge fields are especially limited: only patient first name, last name, full name, and clinic name are static catalog fields. Appointment and recall data can sometimes arrive through trigger metadata, but it is not exposed as a reliable product catalog in the builder.

## Product Diagnosis

### 1. The Builder Is Workflow-Centric, Not Outcome-Centric

Dental teams do not usually think in terms of graph nodes. They think in terms of outcomes:

- Reduce no-shows.
- Fill hygiene openings.
- Bring overdue patients back.
- Convert unscheduled treatment plans.
- Recover cancelled appointments.
- Rebook patients who missed an appointment.
- Follow up after emergency calls.
- Get reviews from happy patients.

The product should start from the outcome, then generate the workflow underneath. The visual builder can remain, but the primary clinic-facing flow should be guided setup.

Recommended direction:

- Add a campaign wizard before the canvas:
  - Goal
  - Audience
  - Timing
  - Channel sequence
  - Message copy
  - Safety/compliance review
  - Preview
  - Launch
- Keep the canvas as an advanced editor or operator view.
- Show estimated audience size, projected send volume, and projected cost before launch.

### 2. Dental Context Is Missing From Messages

A dental appointment reminder without appointment time, provider, location phone, procedure, or confirmation link is too generic. The current static merge fields are not enough for real clinic use.

High-priority merge fields:

- Appointment date
- Appointment time
- Appointment datetime
- Appointment type/procedure
- Provider name
- Operatory, if available and safe to show
- Location name
- Location address
- Location phone number
- Booking link
- Confirmation link
- Reschedule link
- Recall due date
- Last visit date
- Preferred provider
- Insurance plan name, if allowed and useful
- Patient preferred language
- Guardian/parent first name for pediatric workflows

Product impact:

- More personalized messages.
- Less inbound confusion.
- Fewer patient replies asking “when is it?”
- Higher confirmation and booking conversion.

Implementation note:

- The renderer already supports dynamic context values, but the UI should expose a richer catalog by trigger type and channel. Appointment fields should appear for appointment triggers, recall fields for recall triggers, and callback fields for callback workflows.

### 3. Triggers Do Not Cover The Full Dental Lifecycle

The four visible trigger types are enough for an MVP, but dental campaigns need more lifecycle events.

Recommended trigger expansion:

- Appointment booked.
- Appointment cancelled.
- Appointment no-showed.
- Appointment completed.
- Appointment unconfirmed.
- Hygiene recall due.
- Patient overdue by X months.
- Treatment plan presented but unscheduled.
- Treatment plan accepted but not booked.
- New patient created.
- New patient no future appointment.
- Birthday.
- Insurance benefits expiring.
- Inbound call requested callback.
- Web form or lead submitted.
- Broken appointment recovery.

Highest-value first triggers:

1. No-show recovery.
2. Cancellation rebooking.
3. Unscheduled treatment follow-up.
4. Hygiene recall due.
5. Callback requested.

These are directly tied to missed revenue or front-desk workload.

### 4. Segmentation Is The Biggest Missing Product Layer

Right now campaigns are triggered broadly or manually enrolled. A dental campaign tool needs safe targeting.

Segmentation ideas:

- Last visit more than X months ago.
- Has no future appointment.
- Has hygiene due.
- Has unscheduled treatment plan.
- Has cancelled/no-showed in last X days.
- Appointment type is hygiene, emergency, ortho, whitening, consult, etc.
- Provider equals selected provider.
- Location equals selected location.
- Age group: child, adult, senior.
- Preferred language.
- Insurance status.
- New patient vs existing patient.
- Consent available for SMS/email/voice.
- Exclude patients contacted in last X days.
- Exclude patients with open balance, if the clinic wants that rule.

Product requirement:

- Every audience builder should show estimated audience size and exclusions before launch.
- The preview should show sample patients with masked PHI where needed.
- The system should explain why patients were skipped: no consent, no phone, already booked, outside location, duplicate, do-not-contact, quiet hours, etc.

### 5. Campaigns Need Patient Response Handling

Appointment confirmation currently has a condition checking `appointment_status`, but the product needs first-class response handling.

For SMS:

- YES confirms appointment.
- C or CONFIRM confirms appointment.
- R or RESCHEDULE routes to staff or booking link.
- STOP opts out.
- HELP provides clinic contact information.
- Free-text replies create a staff task or conversation item.

For voice:

- Wait for Retell outcome.
- Branch on answered, voicemail, no-answer, busy, booked, callback requested, transferred, do-not-call.
- Create staff handoff when AI cannot complete the task.

For email:

- Track delivered, opened, clicked, bounced, unsubscribed.
- Branch on confirmation link clicked or booking link clicked.

Recommended product feature:

- Add “Patient Response” as a visible workflow concept, not just a generic condition node.
- Add a response inbox/task queue for replies that need human follow-up.
- Show response outcomes in campaign analytics.

### 6. Campaign Analytics Are Essential

Campaigns should not just send messages; they should prove impact. A dental office will ask whether the campaign filled chairs.

Core analytics:

- Enrolled patients.
- Sent attempts by channel.
- Delivered, failed, bounced.
- Open/click rate for email.
- Reply rate for SMS.
- Confirmation rate.
- Booking rate.
- Recall booked rate.
- No-show reduction.
- Cancellation recovery.
- Staff handoffs.
- Opt-outs/unsubscribes.
- Cost by channel.
- Cost per booking.
- Estimated recovered production.

Dental-specific dashboards:

- Appointment confirmation dashboard:
  - Confirmed, not confirmed, cancelled, rescheduled, no response.
- Recall dashboard:
  - Overdue patients contacted, appointments booked, production opportunity recovered.
- Reactivation dashboard:
  - Inactive patients reached, booked, opted out, still inactive.
- Front-desk workload dashboard:
  - Calls avoided, callbacks automated, staff handoffs created.

### 7. Campaign Management Needs Operational Views

The workflow builder is only one part. Clinics need to operate live campaigns.

Recommended pages:

- Campaign overview:
  - Status, audience, latest version, channels, readiness, current runs, recent outcomes.
- Runs/progress:
  - Active, waiting, completed, failed, suppressed.
  - Current step and next scheduled action.
- Patient timeline:
  - Each message/call/email attempt and patient response.
- Enrollment:
  - Manual add, contact selection, CSV validation, audience preview.
- Failures:
  - Failed sends, stuck runs, replay/cancel controls.
- Analytics:
  - Outcome and cost dashboard.

The existing run APIs can support an early version, but the UI needs much richer filtering and outcome visibility.

### 8. Compliance Needs To Become A Product Experience

The current foundation includes consent flags, content class validation, unsubscribe handling, suppression, do-not-contact, quiet hours, and emergency halt. That is important, but clinic admins need understandable guidance.

Recommended improvements:

- Consent status visible before campaign launch.
- Audience exclusions by consent reason.
- Compliance checklist before publish.
- Copy warnings for promotional language in care/recall campaigns.
- PHI-in-message warnings.
- Quiet hours/send-window preview.
- Opt-out rate monitoring.
- “Do not send to patients contacted in last X days” frequency cap.
- TCPA/CASL/HIPAA explanation in plain language for clinic admins.
- Approval workflow for large or marketing campaigns.

Important product principle:

- Compliance should not feel like backend validation errors. It should feel like launch readiness.

### 9. Multi-Location/DSO Controls Are Missing

The platform has a group/institution/location model, but campaigns appear mostly institution/location scoped. DSOs need central control with local customization.

Recommended DSO features:

- Create a campaign template at group level.
- Roll it out to selected locations.
- Allow local overrides:
  - Provider names.
  - Phone numbers.
  - Send windows.
  - Message signature.
  - Booking links.
- Lock critical compliance copy.
- Compare performance across locations.
- Identify underperforming locations.
- Bulk pause/halt across locations.
- Approval flow for local edits.

This becomes important once the product sells beyond single clinics.

### 10. The Visual Builder Needs Better UX, But It Is Not The Top Product Risk

The current builder is usable but still missing several expected editor affordances:

- Undo/redo.
- Auto-layout.
- Better edge/connection editing.
- Version diff.
- Template save/reuse.
- Duplicate node.
- Comment/notes on workflow.
- Search within larger workflows.
- Draft conflict handling for multiple admins.
- Better mobile/tablet fallback.

These matter, but they should not outrank dental-specific campaign outcomes, segmentation, merge fields, and analytics. A beautiful generic builder will still feel weak if campaigns cannot target the right patients or prove results.

## Recommended Product Roadmap

### Phase 1: Make The Existing MVP Useful For Real Dental Campaigns

Goal: Improve the current product without major architecture changes.

Build:

- Expand merge fields for appointment, clinic, provider, booking, and recall context.
- Expose backend-supported `callback_requested` trigger in the frontend.
- Add voice `wait_for_outcome` configuration to the frontend.
- Add campaign detail overview with latest runs, active/waiting/completed/failed counts.
- Add basic run list UI with filters.
- Add campaign cost summary from existing usage-by-campaign API.
- Add audience preview for manual/bulk enrollments.
- Add launch checklist: channels ready, consent basis, quiet hours, estimated audience, estimated cost.
- Add better templates for:
  - No-show recovery.
  - Cancellation rebooking.
  - Callback automation.
  - Unscheduled treatment follow-up.

Why first:

- These changes convert hidden/partial backend capability into visible product value.
- They make demos much stronger.
- They improve real clinic usefulness without waiting for a full segmentation engine.

### Phase 2: Add Dental Segmentation And Response Handling

Goal: Let clinics target the right patients and react to responses.

Build:

- Segment builder with filters for no future appointment, overdue recall, last visit, appointment type, provider, location, preferred language, and consent.
- Exclusion rules: contacted recently, do-not-contact, already booked, already confirmed.
- SMS response parser for YES/CONFIRM/RESCHEDULE/STOP/HELP/free text.
- Staff task/handoff queue for unresolved replies.
- Voice outcome branches using Retell post-call results.
- Email click/booking attribution.
- Patient-level campaign timeline.

Why second:

- Segmentation and response handling are the difference between “message blast” and “workflow automation.”
- These features directly affect patient experience and clinic workload.

### Phase 3: Add Analytics, ROI, And Reporting

Goal: Prove the platform is generating value.

Build:

- Campaign analytics dashboard.
- Daily campaign metric rollups.
- Outcome mapping by campaign type.
- Cost per campaign and cost per booked appointment.
- Confirmation/no-show trend report.
- Recall conversion report.
- Reactivation report.
- Exportable reports for managers.
- Scheduled weekly campaign report email.

Why third:

- Analytics require reliable event and outcome definitions.
- Once response handling exists, analytics become much more meaningful.

### Phase 4: Add Governance And Multi-Location Scale

Goal: Support DSOs and reduce campaign risk.

Build:

- Group-level templates.
- Multi-location rollout.
- Local overrides with locked fields.
- Approval workflow.
- Campaign-level permissions.
- Full audit trail and version diff.
- Budget caps and send caps.
- Cross-location benchmarking.

Why fourth:

- This matters most for enterprise/DSO sales.
- It should build on proven single-location campaign flows.

## Suggested Dental Campaign Library

### Appointment Operations

- 72h appointment confirmation.
- 24h appointment reminder.
- 2h same-day reminder.
- Unconfirmed appointment escalation.
- Cancellation rebooking.
- No-show recovery.
- Pre-op instructions.
- Post-op care follow-up.

### Hygiene And Recall

- 6-month hygiene recall.
- 9-month overdue recall.
- 12-month inactive patient recall.
- Family recall bundling.
- Provider-specific hygiene recall.

### Treatment And Revenue Recovery

- Unscheduled treatment plan follow-up.
- Insurance benefits expiring.
- Whitening/Invisalign consult follow-up.
- Emergency visit follow-up.
- High-value treatment acceptance nurture.

### New Patient And Lead Conversion

- New patient welcome.
- Web lead qualification.
- Missed inbound call callback.
- AI callback from callback queue.
- Consultation reminder.

### Reputation And Retention

- Post-visit satisfaction check.
- Review request after completed appointment.
- Birthday greeting.
- Lapsed patient reactivation.

## Recommended Team Discussion Questions

- Are we building primarily for single-location clinics first, or DSO rollout from day one?
- Which outcome matters most for the next release: fewer no-shows, more recall bookings, more treatment bookings, or less staff workload?
- Which PMS/NexHealth fields are reliably available enough to expose as merge fields and filters?
- Should clinic admins get free-form canvas editing, or should most users go through guided templates?
- What campaign types require approval before publish?
- What is our default contact frequency cap?
- What attribution window should count as a campaign-driven booking?
- What is the minimum analytics dashboard needed for a clinic to believe the product works?

## Priority Ranking

| Priority | Improvement | Reason |
|---|---|---|
| P0 | Rich dental merge fields | Current messages are too generic for real appointment/recall workflows. |
| P0 | Campaign launch checklist | Prevents unsafe or incomplete launches and improves confidence. |
| P0 | Campaign overview + run progress | Users need to know what is happening after launch. |
| P0 | Expose callback trigger and voice outcome settings | Backend capability exists but product surface is incomplete. |
| P1 | Dental campaign template expansion | Makes the product feel purpose-built instead of generic. |
| P1 | Audience preview and exclusions | Reduces accidental outreach and explains skipped patients. |
| P1 | SMS/voice response handling | Converts sends into actual workflow automation. |
| P1 | Basic campaign analytics | Clinics need proof of impact. |
| P2 | Segment builder | Unlocks high-value targeting and personalization. |
| P2 | Cost and ROI dashboard | Helps practices justify spend and compare channels. |
| P2 | Approval/audit/version diff | Needed for safer healthcare operations. |
| P3 | DSO rollout and local overrides | Needed for enterprise scaling. |
| P3 | A/B testing | Useful after baseline analytics and volume exist. |

## Bottom Line

The existing workflow builder foundation is worth keeping. The product should not restart around a new builder library or spend most of its energy on canvas polish. The bigger opportunity is to make the system deeply dental:

- Dental-specific audiences.
- Dental-specific triggers.
- Dental-specific message fields.
- Dental-specific patient responses.
- Dental-specific analytics and ROI.
- Dental-specific compliance and DSO controls.

The next best release should make a clinic admin feel: “I know exactly who this campaign will contact, why, what they will receive, what it will cost, and what result it produced.”
