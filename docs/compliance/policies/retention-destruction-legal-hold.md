# Retention, Destruction, and Legal Hold Policy

Status: Draft
Product: Scale Nexus
Legal entity: TODO
Owner: Founder
Privacy/security contact: issmaeel@scalenexus.ai
Effective date: TODO
Review cadence: At least annually and after material product, vendor, customer,
or legal changes.

## 1. Purpose

This policy defines how Scale Nexus retains, destroys, anonymizes, and suspends
destruction of personal information and personal health information processed
for dental clinics.

## 2. Scope

This policy applies to:

- Call records, transcripts, summaries, tags, custom fields, callback notes,
  and appointment workflow data.
- Call recordings.
- SMS content and metadata.
- Email content sent through Resend, including staff call alerts. Patient-facing
  appointment confirmations must remain disabled until Resend agreement and
  retention settings are confirmed.
- In-app notifications.
- Dead-letter/replay payloads.
- Retell webhook/function idempotency records.
- Audit/security records.
- Backups and vendor-held data where contractual controls are available.

## 3. Principles

- Keep records only as long as needed for service, legal, contractual, clinic,
  security, and audit purposes.
- Destroy or anonymize records when retention expires and no legal hold applies.
- Retain audit/security evidence needed to prove appropriate access, security,
  and breach response.
- Avoid retaining raw payloads and recordings longer than necessary.
- Use clinic-specific instructions where contractually required.

## 4. Default Retention Schedule

| Record type | Default retention |
|---|---:|
| Clinical call record, transcript, summary, custom PHI, appointment outcome | 10 years |
| Minor patient clinical record when DOB is known | Until age 28 if later than 10 years |
| Call recordings | 90 days |
| SMS body/content | 10 years when clinical/care-related |
| SMS metadata | 6 years, unless content retention requires longer row retention |
| Resend auth emails | Authentication/account lifecycle need; TODO finalize |
| Resend staff call alerts | Minimized/redacted; TODO confirm provider-side retention |
| Resend patient appointment confirmations | Disable until vendor retention/agreement is confirmed |
| In-app notification content | 180 days |
| Dead-letter raw replay payload | 30 days |
| Retell webhook/function idempotency rows | 7 days |
| Runtime application logs | 30-90 days target, PHI-free |
| Backups | TODO |
| Audit/security logs | TODO; should be long enough to support investigations and contractual/legal obligations |

## 5. Current Technical Implementation

Current code-level controls include:

- Retention fields on calls, SMS logs, notifications, and dead-letter events.
- Scheduled retention job that purges eligible expired data.
- Legal hold fields that block purge where implemented.
- S3 recording lifecycle tags and default 90-day recording expiry.
- Dead-letter raw payload purge after 30 days.
- Retell idempotency cleanup default of 7 days.

## 6. Call Recordings

Scale Nexus defaults call recordings to 90-day retention. Current business
assumption is that dental clinics do not need recordings retained longer than
90 days.

If a clinic requests longer retention or treats recordings as part of the
official medical/dental record, the clinic contract and retention configuration
must be reviewed before enabling longer retention.

## 7. Legal Hold

Legal hold pauses destruction when records may be needed for:

- Litigation or threatened litigation.
- Regulatory inquiry.
- Security/privacy incident investigation.
- Customer dispute.
- Clinic instruction.

Until a formal self-serve legal hold workflow exists, legal holds must be
tracked manually and implemented by authorized administrative action. Every
legal hold action must be documented with:

- Requesting party.
- Scope of records.
- Reason.
- Start date.
- Expected review date.
- Approver.
- Removal date and approver.

## 8. Destruction and Anonymization

When retention expires and no legal hold applies, Scale Nexus may:

- Delete the record.
- Clear PHI fields while retaining operational metrics.
- Delete S3 recordings.
- Clear raw replay payloads while retaining redacted operational context.

Destruction should be logged without recording PHI in runtime logs.

## 9. Backups

Backup retention and restore-test evidence are TODO. The final policy must
define:

- Backup retention period.
- Restore-test cadence.
- Whether expired data may persist temporarily in backups.
- Maximum backup expiry window.
- Procedure for handling legal holds and restored data.

## 10. Vendor Data

Vendor agreements and settings must support retention and deletion obligations
where possible. Vendor-specific retention settings must be captured for:

- AWS
- Retell
- Twilio
- Resend
- NexHealth

Resend is not purely auth-only in code. It also sends staff call alerts and can
send patient appointment confirmations when a clinic explicitly enables the
patient-facing template.

## 11. Evidence

Evidence for this policy should include:

- Retention configuration.
- Scheduled retention job logs.
- S3 lifecycle configuration.
- Retention tests.
- Legal hold records.
- Destruction/export/offboarding records.
- Vendor retention settings.

## 12. Open Items Before Approval

- Confirm backup retention and restore-test cadence.
- Confirm audit/security log retention.
- Confirm legal hold approver.
- Confirm whether clinics will ever need configurable retention.
- Confirm vendor-side retention and deletion settings.
- Disable patient-facing Resend appointment confirmations before production
  regulated use unless vendor agreement/retention settings are confirmed.
