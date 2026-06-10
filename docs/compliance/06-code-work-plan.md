# Separate Code Work Plan

Status: Draft
Owner: Engineering

This is intentionally separate from the policy work. These items are code or
infrastructure improvements that make the policies easier to operate and prove.

## C-001 Legal Hold Admin Workflow

Goal: Allow authorized admins to pause retention destruction for a clinic,
patient/contact, call, SMS thread, or record set.

Scope:
- API endpoints to set/remove legal hold.
- Dashboard UI for legal hold status.
- Audit log every legal hold action.
- Retention job must skip held records.
- Tests for authorization and purge skip behavior.

Estimate: 2-4 working days.

## C-002 Tenant-Specific Retention Settings

Goal: Allow each clinic/customer to use approved retention windows by contract
or jurisdiction.

Scope:
- Institution-level retention config.
- Validation minimums/maximums.
- Migration/backfill behavior.
- Admin UI.
- Tests for default vs override behavior.

Estimate: 2-4 working days.

## C-003 Access/Correction/Export Workflow

Goal: Support HIPAA/PHIPA/PIPEDA individual rights routed through clinics or
directly where applicable.

Scope:
- Request intake record.
- Export bundle generation.
- Correction request workflow.
- Status tracking.
- Audit logs.
- Role restrictions.

Estimate: 3-6 working days.

## C-004 Clinic Offboarding Return/Delete Workflow

Goal: Support contract termination obligations to return or destroy customer
data.

Scope:
- Export customer data.
- Confirm retention/legal hold constraints.
- Delete/anonymize eligible data.
- Evidence report.
- Tests for tenant isolation.

Estimate: 3-5 working days.

## C-005 Monitoring and Alerting

Goal: Alert on compliance-critical failures.

Scope:
- Retention job failure alarm.
- Audit persistence failure alarm.
- Auth/MFA anomaly alerts.
- Backup failure/restore evidence hooks.
- Dead-letter volume alarm.

Estimate: 2-4 working days.

## C-006 Evidence Automation

Goal: Reduce manual audit prep.

Scope:
- Generate compliance evidence reports.
- Export current retention settings.
- Export access review snapshots.
- Export recent security/audit job status.

Estimate: 4-8 working days.

## Recommended Engineering Order

1. C-007 Disable patient-facing Resend confirmations until vendor terms are confirmed.
2. C-005 Monitoring and alerting.
3. C-001 Legal hold admin workflow.
4. C-002 Tenant retention settings.
5. C-003 Access/correction/export workflow.
6. C-004 Clinic offboarding return/delete workflow.
7. C-006 Evidence automation.

## C-007 Disable Patient-Facing Resend Confirmations

Goal: Prevent PHI-containing patient emails from being sent through Resend
until Resend agreement and retention settings are confirmed.

Current state:
- Staff call alerts are minimized/redacted.
- Patient-facing appointment confirmation emails are gated by an inactive
  template by default, but a clinic can enable the template.

Scope:
- Add a platform setting that defaults patient-facing Resend confirmations to
  disabled.
- Prevent activation/sending unless the setting is explicitly enabled after
  vendor terms are approved.
- Keep staff alerts and auth emails working.
- Add tests proving patient-facing emails cannot send while disabled.

Estimate: 1-2 working days.
