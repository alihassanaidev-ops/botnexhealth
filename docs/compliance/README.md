# Compliance Program Workspace

Status: Draft
Last updated: 2026-05-16

This folder is the working evidence and policy pack for HIPAA, PHIPA, and
PIPEDA readiness. It is not legal advice; counsel should review final versions
before customer or regulator use.

## Current Goal

Build a practical compliance package for a voice-agent platform that handles
clinic calls, scheduling workflows, SMS, in-app/email notifications, call
recordings, and PMS/NexHealth integrations.

## Workstreams

| Workstream | File | Status |
|---|---|---|
| Scope and role mapping | [01-compliance-scope.md](01-compliance-scope.md) | Draft |
| Data inventory | [02-data-inventory.md](02-data-inventory.md) | Draft |
| Data flow and vendors | [03-data-flow-and-vendors.md](03-data-flow-and-vendors.md) | Draft |
| Gap register | [04-gap-register.md](04-gap-register.md) | Draft |
| Policy build plan | [05-policy-plan.md](05-policy-plan.md) | Draft |
| Separate code backlog | [06-code-work-plan.md](06-code-work-plan.md) | Draft |
| Internal access inventory | [07-access-inventory.md](07-access-inventory.md) | Draft |
| Questions for owner | [00-open-questions.md](00-open-questions.md) | Needs answers |

## Baseline Assumptions

- The product name is Scale Nexus.
- The legal company name is not finalized yet.
- The platform is a service provider to dental clinics, not the clinic itself.
- The current go-to-market scope is Canada.
- Ontario should be covered from the start as part of the Canada scope.
- Privacy/security contact email: issmaeel@scalenexus.ai.
- Under HIPAA, the platform expects to act as a Business Associate when serving
  US covered entities.
- Under PHIPA, the platform expects to act as an agent/electronic service
  provider to Ontario health information custodians when serving Ontario
  clinics.
- Under PIPEDA, the platform is accountable for personal information it
  controls and must ensure comparable protection through vendors.
- Patients should normally submit privacy/access/correction requests to their
  clinic; clinics may escalate platform-specific requests to Scale Nexus.
- Call recordings use a 90-day default retention window unless a clinic
  contractually requires otherwise.
- AWS infrastructure is intended to run in Canada. NexHealth data residency is
  not confirmed and must be handled as a vendor-contract/disclosure item.
- Current production access is held by Ismaeel and the development team. Named
  dev-team members identified so far: Ali Hassan and Zulkaif Ahmed. Account
  access levels still need to be documented for access reviews.

## Known Technical Controls Already Present

- Runtime logging is designed not to emit PHI.
- PHI-bearing columns use application-level encryption where implemented.
- Tenant isolation is enforced with RLS and static tests.
- MFA, RBAC, account lockout, and session controls exist.
- Audit logs and related immutability controls exist.
- Retention metadata and scheduled purge logic exist for recordings, SMS
  bodies, notifications, dead-letter raw payloads, and clinical records.
- S3 recordings are private, signed for access, tagged for lifecycle expiry,
  and covered by default 90-day retention.

## Official Reference Anchors

- HIPAA Security Rule: https://www.hhs.gov/hipaa/for-professionals/security/
- HIPAA Business Associate contracts: https://www.hhs.gov/hipaa/for-professionals/covered-entities/sample-business-associate-agreement-provisions/
- PIPEDA fair information principles: https://www.priv.gc.ca/en/privacy-topics/privacy-laws-in-canada/the-personal-information-protection-and-electronic-documents-act-pipeda/p_principle/
- OPC breach guidance: https://www.priv.gc.ca/en/privacy-topics/privacy-breaches/respond-to-a-privacy-breach-at-your-business/
- IPC Ontario breach protocol: https://www.ipc.on.ca/en/health-organizations/responding-to-a-privacy-breach/privacy-breach-protocol
