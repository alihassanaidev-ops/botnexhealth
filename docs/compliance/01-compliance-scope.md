# Compliance Scope

Status: Draft
Owner: Founder
Review cadence: At least annually and after material product/vendor changes.

## Purpose

Define the platform role, regulated data, systems, vendors, and boundaries for
HIPAA, PHIPA, and PIPEDA readiness.

Product name: Scale Nexus.

Legal company name: TODO.

Initial customer segment: dental clinics.

Initial geography: Canada.

Ontario is included in the initial Canadian scope.

Privacy/security contact: issmaeel@scalenexus.ai.

## Role Mapping

| Framework | Expected platform role | Customer role | Current status |
|---|---|---|---|
| HIPAA | Business Associate if serving US clinics later | Covered Entity or Business Associate | Not initial Canada-only focus; requires signed BAA before US PHI processing |
| PHIPA | Agent / electronic service provider when serving Ontario dental clinics | Health Information Custodian | Requires service/agent terms and information-practice alignment |
| PIPEDA | Organization/service provider accountable for personal information it controls | Organization collecting from individuals | Requires privacy program, safeguards, consent/access/correction handling |

## In Scope Systems

- Backend API and worker services in `src/app`.
- PostgreSQL/RDS database.
- Redis/Celery queue.
- S3 call recordings bucket.
- Dashboard frontend where staff access calls, notifications, settings, and
  user/admin workflows.
- Scheduled jobs for audit partitions, rollups, idempotency cleanup, and
  retention policy enforcement.
- Vendor integrations: AWS, Retell, Twilio, Resend/email, NexHealth/PMS.

## In Scope Data

- Patient/caller identifiers: name, phone, email, DOB, PMS/NexHealth patient ID.
- Appointment data: appointment type, provider, location, dates/times,
  booking/cancel/reschedule status.
- Call content: scrubbed transcript, summary, custom analysis fields, tags,
  callback notes, intent, next action, recordings.
- Communications: SMS body and metadata, email alert content, patient-facing
  appointment confirmation email content where enabled, notification
  title/message/data.
- Authentication/security data: users, MFA factors, recovery codes, refresh
  tokens, login failures, audit logs.
- Operational data: webhook/function idempotency, dead-letter events, metrics.

## Out of Scope for This Pack

- Customer clinic EHR/PMS internal systems.
- Clinic workforce policies outside this platform.
- Vendor internal security programs except through contract due diligence.
- Legal advice, regulatory filings, or final legal determinations.

## Compliance Position

Current position after recent code work and planned BAAs:

- HIPAA: technically stronger, but requires risk analysis, written policies,
  BAA/vendor evidence, training records, access reviews, and incident response
  evidence.
- PHIPA: requires clear agent/service-provider terms, information practices,
  safeguards, breach workflow, access/correction support, and retention/disposal
  evidence.
- PIPEDA: requires accountability program, consent/use/disclosure clarity,
  safeguards, retention limits, access/correction process, breach assessment,
  and subprocessor management.

Patient-facing requests should normally be sent to the clinic. Clinics may
escalate platform-specific requests to Scale Nexus.

AWS infrastructure is intended to run in Canada. NexHealth data residency is
unknown and must remain a contract/disclosure item until confirmed.

## Required Signoffs

- Privacy Officer: Founder
- Security Officer: Founder
- Executive owner: Founder
- Legal counsel: TODO
