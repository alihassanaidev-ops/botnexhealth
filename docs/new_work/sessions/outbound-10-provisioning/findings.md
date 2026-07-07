# Findings: Outbound 10 — Per-tenant Provisioning

## 2026-07-08 Scope Decision
CTO confirmed that Plan 10 should not expand into automated setup/onboarding. The original large-plan items
around Twilio sub-account creation, A2P/toll-free registration, Resend DNS/domain automation, warm-up, Secrets
Manager onboarding automation, and a new persisted onboarding/readiness lifecycle are not required for the current
product scope.

The required Plan 10 scope is the operational slice already built: secure credential storage, tenant-aware send
routing, admin configuration/status, and auditability.

## Encryption pattern (Institution model)
`encrypt_value(value)` / `decrypt_value(value)` are module-level functions in
`src/app/models/institution.py` (L86, L121). No separate import. Each encrypted
field is a `Mapped[str | None] = mapped_column(Text, nullable=True)` column, with
a `@property` getter that calls `decrypt_value` and a setter that calls `encrypt_value`.

## Twilio architecture (confirmed)
- Platform creds: `settings.twillio_sid`, `settings.twillio_api_secret`
- `SmsService._get_twilio_client()` constructs `Client(account_sid, auth_token)` — all at platform level
- Per-institution: sub-account SID + auth token stored encrypted on Institution
- `InstitutionLocation.twilio_from_number` already stores the outbound phone number per location
- Fallback to platform creds when institution has no sub-account configured

## Resend / email architecture (confirmed)
- Platform creds: `settings.resend_api_key`, `settings.resend_from_email`, `settings.resend_reply_to`
- For outbound email (Plan 05): will use platform Resend API key, but `from` header
  uses per-institution `email_from_address` / `email_from_name`
- No per-institution Resend API key for v1 (single platform account, multiple verified domains)

## Retell architecture (confirmed by user 2026-07-03)
- Single enterprise Retell account — NOT per-clinic
- `InstitutionLocation.retell_agent_id` already stores the agent ID per location
- No new provisioning needed for Retell in Plan 10

## Admin route pattern
- `src/app/api/routes/admin_institutions.py` — existing SUPER_ADMIN institution management
- New provisioning endpoints fit naturally as subroutes here

## Alembic chain
- Latest migration: `20260703_consent_channel` (from previous session Plan 12)
- Plan 10 migration chains from `20260703_consent_channel`
