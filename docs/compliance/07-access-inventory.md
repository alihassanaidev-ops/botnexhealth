# Internal Access Inventory

Status: Draft
Owner: Founder

This is internal evidence for access reviews. It is not intended for public or
customer-facing policies.

## Access Review Cadence

- Minimum cadence: quarterly.
- Review owner: Founder.
- Evidence to retain: dated copy of this inventory, screenshots or exports from
  AWS/IAM, database/admin tooling, GitHub/deploy tooling, and vendor consoles.
- Review outcome: approved, removed, reduced, or follow-up required.

## Current Named Users

| Person | Email | Role | AWS access | Database access | GitHub/deploy access | Vendor admin access | MFA enabled | Last reviewed | Notes |
|---|---|---|---|---|---|---|---|---|---|
| Ismaeel | TODO | Founder/privacy-security owner | TODO | TODO | TODO | TODO | TODO | TODO | Account details needed |
| Ali Hassan | TODO | Developer | TODO | TODO | TODO | TODO | TODO | TODO | Account details needed |
| Zulkaif Ahmed | TODO | Developer | TODO | TODO | TODO | TODO | TODO | TODO | Account details needed |

## Review Checklist

- Confirm each account is assigned to a named person.
- Confirm MFA is enabled wherever the service supports it.
- Confirm production access is least privilege for the person's role.
- Remove access for inactive users and contractors.
- Remove shared credentials unless there is a documented break-glass procedure.
- Confirm vendor admin consoles are included, not only AWS and GitHub.
- Record exceptions with owner, reason, expiry date, and compensating control.

