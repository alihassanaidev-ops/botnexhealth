# Part 10 - Per-Tenant Messaging Provisioning Implementation Plan

## What Needs To Be Built

Build provisioning and credential management for each clinic's messaging infrastructure: Twilio sub-accounts and sender numbers for voice/SMS, region-appropriate A2P/toll-free verification status, per-clinic email sending domains, and outbound Retell workspace/BYO telephony binding where applicable. The system must store credentials securely, expose setup status, and route outbound sends through the correct tenant-owned resources.

## Existing System Context

The backend currently has:

- `InstitutionLocation.twilio_from_number`.
- Admin Twilio routes that list/send through platform-level Twilio credentials.
- `SmsService` using platform-level `settings.twillio_sid` and `settings.twillio_api_secret`.
- `EmailNotificationService` using platform-level `RESEND_API_KEY` and `RESEND_FROM_EMAIL`.
- Retell agent ids stored per location, but provisioning is manual and inbound-oriented.
- Institution/location setup APIs and frontend setup pages.
- Encrypted field pattern on `Institution.nexhealth_api_key_encrypted`.
- AWS Secrets Manager already used by infra for platform secrets.

Current gaps:

- No Twilio sub-account model.
- No per-location Twilio credentials.
- No A2P/10DLC/toll-free verification status.
- No per-clinic email domain/sending profile.
- No automated or tracked Retell workspace/BYO SIP provisioning.
- No provider health/readiness model covering voice/SMS/email.

## Existing Components To Reuse

- Institution/location models and setup UI patterns.
- Encrypted credential property patterns from `Institution`.
- Existing Twilio route/service patterns, adapted to tenant credentials.
- Existing email template UI and service patterns.
- Audit logging for provisioning/configuration changes.
- Role gates: super-admin for platform provisioning, institution/location admins for read-only status and limited configuration.
- CDK/AWS Secrets Manager patterns for production secrets.

## New Components Required

### Data Model

- `twilio_tenant_accounts`
  - `institution_id`, optional `location_id`
  - account SID
  - encrypted auth token or Secrets Manager ARN
  - parent platform account SID if sub-account
  - status: `pending`, `active`, `suspended`, `failed`
  - region/country
  - created/provisioned timestamps

- `twilio_sender_numbers`
  - `institution_id`, `location_id`
  - phone number, number SID
  - capabilities: sms, voice, mms
  - verification type: `a2p_10dlc`, `toll_free`, `none`
  - verification status
  - assigned Retell/SIP binding status
  - active flag

- `a2p_registration_records`
  - business/brand identifiers
  - campaign identifiers
  - status and rejection reasons
  - submitted/approved timestamps
  - jurisdiction/country

- `email_sending_profiles`
  - described in Part 5
  - domain verification status and DNS records

- `retell_tenant_voice_profiles`
  - workspace id
  - outbound agent id
  - SIP/BYO telephony binding status
  - credential secret reference
  - active flag

- `messaging_readiness_checks`
  - materialized setup status per location/channel
  - last checked at, failures, warnings

### Services

- `MessagingProvisioningService`
  - orchestrates setup status and provider resources
  - creates/updates records
  - exposes readiness for SMS, voice, and email

- `TenantTwilioCredentialResolver`
  - returns the correct Twilio credentials for a location
  - supports fallback only where explicitly allowed
  - used by `SmsService`, Twilio webhook validation, and voice/SIP provisioning

- `TwilioProvisioningClient`
  - wraps Twilio sub-account, number, brand, campaign, and verification APIs
  - must be mockable because real provisioning is slow and provider-stateful

- `EmailDomainProvisioningService`
  - creates and verifies sending domains (SPF/DKIM + **explicit DMARC** onboarding step)
  - stores required DNS records
  - marks send-ready only after verification
  - **tracks per-domain warm-up state** and gates bulk sending until warmed (or starts the clinic
    on a warmed shared/subdomain and graduates) — a launch deliverability control, not a footnote
    (Finding 6). Bulk-email readiness in `messaging_readiness_checks` reflects warm-up, not just DNS.

- `RetellProvisioningTracker`
  - tracks manual or API-assisted workspace/agent/SIP setup
  - validates outbound agent id and binding status when API allows

## End-To-End Implementation Approach

1. Add provisioning tables and readiness status API.
2. Add admin setup screens/status panels for each channel.
3. Implement credential resolver and update SMS sending to use tenant Twilio credentials.
4. Update Twilio webhook signature validation to resolve by destination number and validate with that sub-account token.
5. Add Twilio sender/account status sync job.
6. Add A2P/toll-free verification records and manual override/status update flow if full automation is not feasible.
7. Add email sending profiles and provider domain verification sync.
8. Add Retell outbound voice profile tracking for Part 3.
9. Block workflow publish or campaign activation when required channel readiness is missing.
10. Add audits for all credential/status/config changes.

## Architecture Decisions

- Provisioning state is first-class data, not only environment variables. Campaign activation depends on it.
- Store secrets encrypted or as AWS Secrets Manager references. Prefer Secrets Manager references for production rotation and least exposure.
- Resolve provider credentials by location at send time. This keeps multi-location institutions from accidentally sharing sender identity.
- Support manual provisioning status where vendor APIs or approvals are not fully automatable. The repo already has manual Retell setup; the plan should track it before automating it.
- Keep region rules explicit: US 10DLC, Canada toll-free verification/CASL-oriented setup.

## Technical Considerations

- Existing setting names use `twillio_*` spelling. New code should avoid spreading that typo into new domain APIs, while maintaining compatibility with existing config.
- Twilio sub-account webhooks can be signed with each sub-account auth token. Validation must select the right token after mapping destination number to location/account.
- Status callbacks must include enough route metadata or destination number to resolve the tenant.
- Some provisioning operations can take days. UI must represent pending/rejected states clearly and not imply readiness.
- Email domain DNS verification depends on external DNS changes and should be polled or refreshed on demand.
- Retell workspace and BYO SIP automation depends on Retell API capabilities; if not fully available, track manual checklist completion.
- **Correction (Finding 5): Retell dials both US and Canada natively.** The earlier assumption that
  BYO/SIP is *required* for Canadian outbound is false. Retell-provisioned numbers suffice for
  US + Canada; **BYO-SIP is an optional preference** (bind a clinic's own Twilio sub-account numbers
  / use own telephony), not a Canadian necessity. Treat per-clinic BYO-SIP as optional in v1, which
  simplifies Canadian go-live. (One item to confirm with Retell: whether a workspace is a guaranteed
  PHI/BAA isolation boundary and whether multiple parent Twilio accounts under one Retell account
  are supported.)

## Dependencies

- Outbound SMS, email, and voice implementation.
- Vendor account/API access and business approval workflows.
- AWS Secrets Manager integration for tenant credentials.
- Admin frontend setup surfaces.
- Compliance decisions on who may view/edit provisioning data.

## Edge Cases

- Twilio sub-account active but sender number not SMS-capable.
- 10DLC campaign rejected after campaigns were configured.
- Toll-free verification pending for a Canada-first clinic.
- A phone number is reassigned from one location to another.
- Webhook arrives for a number no longer assigned.
- Tenant credential rotation invalidates cached clients.
- Email domain verified, then DNS record removed later.
- Retell outbound profile exists but SIP binding is broken.
- Multi-location institution wants shared email domain but distinct SMS numbers.

## Risks

- Full automation of A2P/10DLC can be slower and more complex than product timelines allow.
- Credential resolver mistakes can route a patient's message through the wrong clinic's account.
- Per-tenant credentials increase secret rotation and incident-response burden.
- Provider approval delays can block campaign launch for specific locations.
- UI may expose sensitive provider identifiers if not role-gated carefully.

## Validation Strategy

- Unit tests for credential resolution by location.
- Unit tests for readiness calculation by channel.
- Unit tests for Twilio webhook validation with per-sub-account tokens.
- Integration tests proving SMS sends use tenant credentials when present.
- RLS tests for provisioning tables.
- Admin route tests for role-gated provisioning updates.
- Manual staging test with one Twilio sub-account and one verified sender.
- Manual email domain verification flow test in staging.

## Deployment Considerations

- Add provisioning data model before switching send paths.
- Keep platform-level credentials as a controlled fallback during migration, visible in readiness status.
- Migrate existing `twilio_from_number` values into `twilio_sender_numbers`.
- Add feature flags per channel for tenant-credential routing.
- Add metrics for readiness failures, credential lookup failures, webhook validation failures, and provider status sync errors.
- Add runbooks for Twilio registration rejection, credential rotation, number reassignment, email DNS verification, and Retell SIP issues.

## Future Extensibility

- Self-serve clinic onboarding for SMS/email readiness.
- Automated Twilio sub-account creation and campaign registration.
- Automated Retell workspace/agent cloning when API support is sufficient.
- DSO-level provisioning templates inherited by locations.
- Budget caps tied to each tenant provider account.
