# Privacy and Information Practices Policy

Status: Draft
Product: Scale Nexus
Legal entity: TODO
Owner: Founder
Privacy/security contact: issmaeel@scalenexus.ai
Effective date: TODO
Review cadence: At least annually and after material product, vendor, or legal
changes.

## 1. Purpose

Scale Nexus provides a voice-agent and clinic workflow platform for dental
clinics. This policy defines how Scale Nexus collects, uses, discloses,
protects, retains, and disposes of personal information and personal health
information handled through the platform.

This policy supports readiness for Canadian privacy obligations, including
PIPEDA and applicable provincial health privacy obligations such as PHIPA for
Ontario dental clinics.

## 2. Scope

This policy applies to:

- Backend services, workers, scheduled jobs, dashboard, databases, recording
  storage, logs, and vendor integrations used by Scale Nexus.
- Personal information and personal health information processed for dental
  clinics.
- Employees, contractors, founders, support personnel, and vendors who may
  access systems or data.

This policy does not replace the dental clinic's own privacy obligations to its
patients.

## 3. Roles and Accountability

Dental clinics remain primarily responsible for their patient relationship and
for responding to patient access, correction, complaint, and consent requests.

Scale Nexus acts as a service provider/agent to clinics when processing clinic
patient information. Scale Nexus uses patient information only to provide,
secure, support, troubleshoot, and improve the contracted service, unless a
different use is explicitly authorized by the clinic and permitted by law.

The Founder is currently accountable for the privacy and security program until
formal privacy/security officer appointments are finalized.

## 4. Information Collected or Processed

Scale Nexus may process:

- Patient/caller identity: name, phone number, email, date of birth, PMS or
  NexHealth patient identifiers.
- Appointment information: provider, location, appointment type, time, booking,
  cancellation, and rescheduling status.
- Call information: scrubbed transcript, summary, tags, callback notes, patient
  intent, next action, and recordings.
- Communications: SMS body and metadata, in-app notifications, and email
  notifications. Patient-facing appointment confirmation emails must remain
  disabled until Resend agreement and retention settings are confirmed.
- Clinic user/security data: account identity, role, MFA status, login events,
  audit events, and operational metadata.

Scale Nexus minimizes collection and avoids storing raw, unnecessary, or
unapproved patient data where possible.

## 5. Use of Information

Scale Nexus uses information to:

- Answer and route clinic calls through the voice-agent workflow.
- Look up patients and appointment availability through PMS/NexHealth.
- Book, cancel, or reschedule appointments when authorized by clinic workflow.
- Notify clinic staff of calls, callbacks, appointments, and operational events.
- Send SMS or email communications initiated by authorized clinic workflows.
- Secure the platform, investigate incidents, maintain audit logs, and support
  reliability.
- Comply with contractual, legal, and security obligations.

Scale Nexus does not permit workforce members or vendors to use patient
information for unrelated purposes.

## 6. Disclosure and Vendors

Scale Nexus uses vendors only where needed to provide the service. Current
production vendors identified for this program are:

- AWS
- Retell
- Twilio
- Resend
- NexHealth

Each PHI/PII-touching vendor must be reviewed and covered by appropriate
contract terms before regulated production use. Required terms should address
confidentiality, safeguards, breach/security incident notification, retention,
deletion/return, subprocessors, and no unauthorized secondary use or model
training.

## 7. Patient and Clinic Requests

Patients should send access, correction, deletion, complaint, or privacy
questions to their dental clinic first. Clinics may submit platform-specific
requests to Scale Nexus.

Scale Nexus will support clinics by locating, exporting, correcting, retaining,
or deleting eligible platform-held records as required by contract, law, and
legal hold obligations.

Direct patient requests received by Scale Nexus should be logged and routed
back to the appropriate clinic unless Scale Nexus is legally required or
contractually authorized to respond directly.

## 8. Cross-Border Processing

Initial go-to-market scope is Canada. AWS infrastructure is configured for
Canada where applicable, but some vendors may process or support data outside
Canada depending on their service terms.

NexHealth data residency is not confirmed and must be disclosed or addressed
in customer/vendor terms where required.

Customer contracts and clinic-facing notices should disclose cross-border
processing where applicable.

## 9. Safeguards

Scale Nexus maintains safeguards including:

- MFA, RBAC, account lockout, and session controls.
- Tenant isolation and database row-level security.
- Application-level encryption for PHI-bearing fields where implemented.
- Private S3 storage and signed recording access.
- PHI-free runtime logging rules.
- Audit logging for sensitive actions.
- Retention and destruction controls.
- Vendor review and contractual controls.

## 10. Retention and Disposal

Scale Nexus retains records only as long as needed for service, legal,
contractual, security, and clinic obligations. Current platform defaults are
defined in the Retention, Destruction, and Legal Hold Policy.

Call recordings default to 90 days unless a clinic requires a different
contractual retention period.

## 11. Incidents and Breaches

Suspected privacy or security incidents must be reported to the Founder
immediately. Scale Nexus will investigate, contain, document, and notify
affected clinics according to the Incident Response and Breach Notification
Policy.

Clinics remain responsible for patient/regulator notification unless contract
or law assigns that duty differently.

## 12. Evidence

Evidence for this policy should include:

- Vendor agreement tracker.
- Data inventory and data flow records.
- Retention job evidence.
- Access review records.
- Security training acknowledgements.
- Incident logs and tabletop exercises.
- Customer request logs.

## 13. Open Items Before Approval

- Confirm legal company name.
- Confirm vendor agreement status.
- Disable patient-facing Resend appointment confirmations before production
  regulated use unless Resend agreement/retention settings are confirmed.
- Confirm province-specific launch order and any customer-specific residency
  commitments.
