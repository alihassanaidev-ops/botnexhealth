# Open Questions

Status: Needs owner input

Answer these before policies are finalized. Drafts can proceed with TODO
placeholders, but these affect legal scope, notices, vendor terms, and customer
contracts.

## Confirmed Answers

- Product name: Scale Nexus.
- Legal company name: not known yet.
- Customer segment: dental clinics.
- Initial geography: Canada.
- Ontario should be handled from the start as part of Canada coverage.
- Privacy/security owner for now: Founder.
- Privacy/security contact email: issmaeel@scalenexus.ai.
- Production vendors identified: AWS, Retell, Twilio, Resend, NexHealth.
- Patient privacy/access/correction requests should go to the clinic first;
  clinics can escalate platform-specific requests to Scale Nexus.
- Current recording retention assumption: clinics do not need call recordings
  longer than 90 days.
- AWS infrastructure runs in Canada where configured; NexHealth data residency
  is unknown.
- Resend code sends auth emails, staff call notifications, and a gated
  patient-facing appointment confirmation when a clinic explicitly activates
  that template.
- Decision: patient-facing Resend appointment confirmations should be disabled
  until Resend agreement/retention settings are confirmed.
- Production access is currently held by Ismaeel and the development team.
  Named dev-team members identified so far: Ali Hassan and Zulkaif Ahmed.
- Production access evidence should be a named internal access inventory
  covering people/accounts with AWS, database, GitHub/deploy, and vendor admin
  access. This does not need to appear in public/customer-facing policies.
- CDK stack does not provision CloudTrail. Existing deployment docs say
  CloudTrail must be enabled at the AWS account level.
- Backup configuration exists in CDK/RDS config, but restore-test status is
  unknown.
- DPA/agent/service-provider agreement status is unknown.

## Company and Product

1. What is the exact legal company name?
2. Should policies use `issmaeel@scalenexus.ai` for all privacy/security
   requests, or separate privacy/security/support aliases?

## Customers and Contracts

3. Do you sign contracts directly with individual clinics, DSOs/MSOs, or both?
4. Should contracts require Canadian data residency "where possible" while
   disclosing vendor exceptions such as NexHealth?

## Vendors

5. Confirm DPA/agent/service-provider agreement status for AWS, Retell,
   Twilio, Resend, and NexHealth.
6. Does Retell store or train on call audio/transcripts, or is retention
   disabled/contractually restricted?
7. Does Twilio store message bodies, call metadata, or recordings under your
   account? If yes, what retention is configured?
8. Are any monitoring, analytics, support chat, ticketing, or CRM tools active
    in production?

## Operations

9. Complete the internal access inventory for Ismaeel, Ali Hassan, and Zulkaif
   Ahmed: email, role, AWS access, database access, GitHub/deploy access,
   vendor admin access, MFA enabled, and last reviewed date.
10. Confirm whether CloudTrail is enabled manually in every production AWS
   account.
11. Are backups restored/tested today? If yes, how often?
12. Do employees/contractors receive privacy/security training?
13. Is there a documented incident response owner and escalation path?

## Product Rights Workflows

14. How should clinics request export/return/delete at contract end?
15. Do clinics need self-serve legal hold?
16. Should each clinic configure retention, or should platform defaults apply?
