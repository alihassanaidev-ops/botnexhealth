# Compliance Gap Register

Status: Draft
Owner: Founder

Severity key:
- Critical: blocks regulated production use.
- High: material customer/regulatory risk.
- Medium: important but can be time-bound with compensating controls.
- Low: polish/evidence maturity.

| ID | Gap | Frameworks | Severity | Owner | Target | Status |
|---|---|---|---|---|---|---|
| G-001 | Execute customer/vendor agreements for PHI-touching vendors and clinics | HIPAA, PHIPA, PIPEDA | Critical | Founder | TODO | Open |
| G-002 | Complete formal security risk analysis and risk treatment plan | HIPAA, PHIPA, PIPEDA | Critical | Founder | TODO | Open |
| G-003 | Approve privacy/security policy set | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-004 | Create incident response and breach notification runbook with owner/escalation | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-005 | Document access/correction/complaint request workflow routed clinic-first | PHIPA, PIPEDA, HIPAA | High | Founder | TODO | Open |
| G-006 | Establish quarterly access review process and first review evidence | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-007 | Establish workforce privacy/security training and acknowledgement records | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-008 | Confirm backup restore test and document RTO/RPO | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-009 | Add monitoring/alerts for retention job, audit persistence, auth anomalies, backups | HIPAA, PHIPA, PIPEDA | Medium | Engineering | TODO | Open |
| G-010 | Build legal hold admin workflow | HIPAA, PHIPA, PIPEDA | Medium | Engineering | TODO | Code backlog |
| G-011 | Build tenant-specific retention settings | PHIPA, PIPEDA, HIPAA | Medium | Engineering | TODO | Code backlog |
| G-012 | Build export/return/delete workflow for clinic offboarding | HIPAA, PHIPA, PIPEDA | Medium | Engineering | TODO | Code backlog |
| G-013 | Confirm Retell/Twilio/Resend retention and no-training/no-secondary-use settings | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-014 | Create evidence repository with dated screenshots/config exports/test outputs | HIPAA, PHIPA, PIPEDA | Medium | Founder | TODO | Open |
| G-015 | Run tabletop breach exercise and record results | HIPAA, PHIPA, PIPEDA | Medium | Founder | TODO | Open |
| G-016 | Complete AWS/database/deploy/vendor access inventory for Ismaeel, Ali Hassan, and Zulkaif Ahmed | HIPAA, PHIPA, PIPEDA | High | Founder | TODO | Open |
| G-017 | Disable patient-facing Resend appointment confirmations until Resend agreement/retention settings are confirmed | PHIPA, PIPEDA, HIPAA | High | Engineering | TODO | Code backlog |
| G-018 | Confirm CloudTrail is enabled manually or add CloudTrail to CDK/account baseline | HIPAA, PHIPA, PIPEDA | High | Founder/Engineering | TODO | Open |

## Recently Reduced Gaps

- Runtime PHI logging reduced.
- Retention enforcement implemented for key PHI surfaces.
- Tests added for retention logic.
- Recording lifecycle default added.
- Dead-letter raw payload retention added.
