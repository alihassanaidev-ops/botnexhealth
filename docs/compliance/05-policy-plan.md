# Policy Plan

Status: Draft
Owner: Founder

Build plan for the policy set. Each policy should be concise, assign an owner,
set rules, and list its evidence artifacts. Two are drafted so far (see
[policies/](policies/)); the rest are queued behind the open inputs below.

| # | Policy | Covers | Status | Blocking inputs |
|---|---|---|---|---|
| 1 | Privacy and Information Practices | Collection, use, disclosure, request routing, cross-border notice | [Drafted](policies/privacy-and-information-practices.md) | Legal company name; confirm single privacy/security contact alias |
| 2 | Security Management | Security officer, annual risk analysis, risk treatment, sanctions | Not started | Name Founder personally or by role |
| 3 | Access Control and MFA | RBAC, MFA, least privilege, joiner/mover/leaver, quarterly reviews, lockout | Not started | Admin-user approver; completed [access inventory](07-access-inventory.md) |
| 4 | Audit Logging and Monitoring | PHI-free runtime logs, audit event types, immutability, alert review | Not started | Alert destinations, reviewer, response-time targets |
| 5 | Retention, Destruction, Legal Hold | Retention schedule, destruction evidence, hold override | [Drafted](policies/retention-destruction-legal-hold.md) | Backup retention; legal hold approver |
| 6 | Incident Response and Breach Notification | Severity levels, triage, containment, notification decisions, post-incident review | Not started | 24/7 contact path; legal counsel; notification owner |
| 7 | Vendor and Subprocessor Management | Pre-use review, required agreements, breach notice, annual review | Not started | Agreement status per vendor (see [03](03-data-flow-and-vendors.md)) |
| 8 | Backup, DR, Business Continuity | Backup scope/retention, restore testing, RTO/RPO, recovery roles | Not started | Current backup retention; RTO/RPO targets |
| 9 | Workforce Training and Sanctions | Required training, acknowledgement, refresh cadence, contractors | Not started | Workforce list; training process; sanctions authority |
| 10 | Secure SDLC and Change Management | Code review, tests, dependency/secret scanning, migration review, deploy approval | Not started | Branch/release process; production deploy approver |

The retention schedule in policy 5 mirrors the platform defaults
(`RETENTION_*` settings): clinical records 10 years (minors: until age 28 when
DOB known), recordings 90 days, SMS body 10 years / metadata 6 years,
notifications 180 days, dead-letter raw payloads 30 days, idempotency rows
7 days.
