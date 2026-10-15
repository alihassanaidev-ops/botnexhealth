# Staging and Development Data Policy

**Applies to:** the ScaleNexus platform (`botnexhealth`), the GoTracker
Synchronizer and the GoTracker Cloud Service.
**Owner:** engineering.
**Status:** in force.

This platform handles patient health information (PHI). The rules below keep
production patient data out of development and staging, and keep all data in
Canada.

## 1. Synthetic data only in development and staging

- Development and staging databases contain **synthetic or de-identified data
  only**. Never load a production database, a production snapshot, or a real
  patient export into a development or staging environment.
- Test fixtures, seed scripts and demo tenants must use clearly fake names,
  phone numbers and identifiers (for example the reserved `555-01xx` range).
- If a realistic dataset is needed for a demo or load test, it must be generated
  or irreversibly de-identified before it is used, and recorded as such.

## 2. No patient data on developer machines

- No production PHI is stored on individual developer laptops, local databases,
  local `.env` files, notebooks, or chat/AI tools.
- Local development runs against the Docker Compose stack with synthetic data,
  or against a staging database that already complies with section 1.
- Screenshots, logs and bug reports must not contain real patient identifiers.
  Redact before sharing.

## 3. No patient data outside Canada

- Production patient data is processed and stored only in Canadian regions.
- Do not copy PHI to services, vendors, or regions outside Canada without an
  approved Business Associate Agreement and a documented decision.

## 4. All environments live in AWS ca-central-1

- Production and staging both run in AWS `ca-central-1` (Canada Central).
- The GoTracker Cloud Service and its database also run in `ca-central-1`.
- No environment is provisioned in another region.

## 5. Enforcement

- The production deploy path and the retention policy service treat these rules
  as requirements.
- A developer who needs production data for a bug must reproduce the bug with
  synthetic data instead, or work with a named engineer on a controlled,
  audited session in the Canadian staging environment.
- Breaches are handled as incidents.
