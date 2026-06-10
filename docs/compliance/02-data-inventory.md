# Data Inventory

Status: Draft
Owner: Founder

## Data Classes

| Class | Examples | Sensitivity | Primary storage | Retention default |
|---|---|---|---|---|
| Patient identity | Name, phone, email, DOB, PMS patient ID | PHI/PII | PostgreSQL encrypted fields and scoped plaintext display fields | Clinical record window |
| Appointment data | Provider, location, appointment type, time, status | PHI/PII | PostgreSQL, NexHealth/PMS | Clinical record window |
| Call transcript/summary | Scrubbed Retell transcript, call summary, tags | PHI | PostgreSQL encrypted fields | 10 years / minor extension |
| Call recording | Audio recording stored in S3 | PHI | Private S3 bucket | 90 days default; current assumption is clinics do not need longer recording retention |
| SMS content | Outbound body, inbound keyword, status | PHI/PII | PostgreSQL encrypted body, Twilio vendor account | 10 years for clinical body |
| SMS metadata | SID, status, phone hash/masked number | PHI/PII metadata | PostgreSQL | 6 years unless body retention requires longer row retention |
| Email content | Invite/reset emails, staff call alerts, and gated patient appointment confirmations | May contain PHI | Resend/email provider, app templates/logs | Staff alerts minimized/redacted; patient confirmations only if clinic enables template; TODO confirm provider retention/agreement |
| In-app notifications | Title, message, data payload | PHI | PostgreSQL encrypted fields | 180 days |
| Audit logs | Actor, action, target, outcome, metadata | Security/compliance record; may contain references | PostgreSQL append-only/partitioned | Retain for audit/legal period; do not mutate |
| Dead-letter events | Redacted payload, encrypted raw replay payload | May contain PHI | PostgreSQL | Raw payload 30 days; event row operational retention |
| Auth/security data | Users, roles, MFA factors, tokens, login failures | Security sensitive | PostgreSQL | Security/account lifecycle; TODO policy finalization |

## Current Technical Safeguards

- Application-level encryption for several PHI fields.
- Hashing/masking for phone lookup and logs.
- RLS and tenant-scope tests.
- RBAC/MFA/account lockout.
- Audit logging for PHI reveal and admin/security events.
- PHI log sanitization.
- Retention metadata and scheduled purge job.
- Private S3 recordings with signed access.

## Data Stores

| Store | Data | Encryption | Notes |
|---|---|---|---|
| PostgreSQL/RDS | Core app data, PHI, audit logs, auth data | RDS encryption plus app-level field encryption where implemented | RLS enforced |
| S3 recordings bucket | Call recordings | S3-managed encryption, private bucket | Lifecycle by retention tag |
| Redis | Queue/session/cache data | Transit/at-rest depends infra config | Should not store PHI beyond short-lived operational use |
| CloudWatch logs | Runtime logs | AWS-managed | PHI should not be logged |
| Vendor systems | Retell/Twilio/NexHealth/Resend | Vendor-specific | Requires agreements and retention controls |

## Data Minimization Rules

- Do not log raw patient identifiers, transcripts, message bodies, emails, DOBs,
  phone numbers, recording URLs, or vendor payload bodies.
- Store only scrubbed transcript content from Retell.
- Keep recordings short-retention unless the clinic explicitly treats them as
  part of the medical record.
- Retain audit logs and security events for accountability, but keep payloads
  minimized.

## TODO Verification

- Confirm whether patient-facing Resend appointment confirmations should stay
  enabled as an option before Resend agreement/retention is confirmed.
- Confirm Retell retention/training settings.
- Confirm Twilio body retention and status callback payload retention.
- Confirm NexHealth contract/data processing obligations.
- Confirm backup retention and restore-test evidence.
