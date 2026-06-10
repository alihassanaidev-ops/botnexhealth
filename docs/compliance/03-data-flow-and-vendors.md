# Data Flow and Vendors

Status: Draft
Owner: Founder

## High-Level Data Flow

1. Patient calls clinic phone number or interacts with a voice agent.
2. Retell processes the voice interaction and sends signed webhooks/function
   calls to the backend.
3. Backend uses scoped clinic/location mapping to access PMS/NexHealth data.
4. Backend stores scrubbed call records, summaries, selected custom fields,
   and optional recording references.
5. Backend sends outbound SMS through Twilio when clinic staff or workflows
   initiate messages.
6. Backend sends email/in-app notifications to authorized clinic users.
7. Clinic staff access dashboard data through authenticated, MFA-protected
   sessions.
8. Scheduled jobs maintain audit partitions, dashboard rollups, cleanup, and
   retention/destruction.

## Vendor/Subprocessor Register

| Vendor | Purpose | Data exposed | Agreement required | Status |
|---|---|---|---|---|
| AWS | Hosting, RDS, S3, CloudWatch, ECS, Secrets Manager | PHI/PII in database/storage/log metadata | DPA/BAA-equivalent cloud terms as applicable | Active; agreement status TODO |
| Retell | Voice agent/audio/transcript processing | Audio, transcript, caller/appointment context | DPA/service terms with no-training/no-unapproved-retention where possible | Active; agreement/settings TODO |
| Twilio | SMS and telephony webhooks | Phone numbers, SMS bodies, status metadata | DPA/service terms; retention settings | Active; agreement/settings TODO |
| NexHealth | PMS API/scheduling/patient lookup | Patient and appointment data | Customer/vendor agreement path; data processing terms | Active; agreement status TODO |
| Resend/email provider | Auth emails, staff call alerts, gated patient appointment confirmations | Email addresses, redacted/minimized staff alert details, and unredacted appointment confirmation details when patient-facing template is enabled | DPA/service terms required if PHI-containing emails remain enabled | Active; disable patient-facing confirmations until agreement/settings are confirmed |
| Domain/DNS/CDN providers | Frontend/API routing | Metadata, TLS certificates | Security review | TODO confirm |
| Monitoring/error tracking | Observability | Logs/errors; should be PHI-free | DPA/BAA if PHI possible | TODO identify active tools |

## Cross-Border/Residency Notes

- Initial go-to-market geography is Canada, including Ontario.
- AWS region appears configured for `ca-central-1` in current app config.
- Vendor processing may occur outside Canada depending on Retell, Twilio,
  Resend, and NexHealth terms.
- NexHealth data residency is not confirmed.
- PIPEDA and PHIPA customer notices/contracts should disclose cross-border
  processing where applicable.

## Vendor Minimum Requirements

- Signed agreement before PHI processing.
- Confidentiality and safeguard obligations.
- Breach/security incident notice obligations.
- Subprocessor disclosure and change notice where available.
- Data return/delete support at contract end.
- Retention limits for payloads, recordings, logs, and backups.
- No model training or secondary use of PHI without explicit approved terms.

## TODO Verification Checklist

- Obtain executed agreements and store links in the evidence pack.
- Capture screenshots/config exports for vendor retention settings.
- Confirm each vendor support path for breach/security notices.
- Confirm if any vendor sends support data to additional subprocessors.
- Disable patient-facing Resend appointment confirmations until Resend
  agreement/retention settings are confirmed.
