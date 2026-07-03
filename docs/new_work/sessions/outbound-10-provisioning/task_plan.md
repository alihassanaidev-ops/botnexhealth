# Task Plan: Outbound 10 — Per-tenant Provisioning

## Goal
Store and use per-institution messaging credentials so each clinic sends from
their own Twilio sub-account number and their own email from-address, while
Retell stays as a single enterprise account (no per-clinic provisioning needed).

## Current Status
**Complete** ✅ — all 4 slices shipped.

## Existing Infrastructure (confirmed)
- `Institution.nexhealth_api_key_encrypted` — encrypted pattern with local `encrypt_value`/`decrypt_value`
- `InstitutionLocation.twilio_from_number` — outbound SMS number already on location model
- `InstitutionLocation.retell_agent_id` — Retell agent per location (single enterprise account)
- Platform Twilio: `settings.twillio_sid` / `settings.twillio_api_secret`
- Platform Resend: `settings.resend_api_key` / `settings.resend_from_email` / `settings.resend_reply_to`
- `SmsService._get_twilio_client()` currently uses platform credentials only

## Decisions

| # | Decision | Resolved | Notes |
|---|----------|----------|-------|
| D1 | Twilio model: per-institution sub-account vs shared platform | ✅ **Sub-account per institution** | Scope doc + user confirmed |
| D2 | Twilio fallback: if no institution creds, use platform? | ✅ **Yes, fall back** | Phased rollout; empty field = platform creds |
| D3 | Email model: per-institution Resend API key vs platform key + per-institution domain | ✅ **Platform key + per-institution from-address** | Resend supports multiple domains under one account; per-institution API key deferred |
| D4 | Retell: per-clinic provisioning? | ✅ **None needed** | Single enterprise account, `retell_agent_id` per location already exists |
| D5 | Admin API auth: who can configure credentials? | ✅ **SUPER_ADMIN only** | These are platform-managed credentials, not clinic-admin managed |

## Slices

### Slice 1 — Institution credential fields + migration ✅
- [x] Add to `Institution` model: `twilio_account_sid_encrypted`, `twilio_auth_token_encrypted`, `email_from_address`, `email_from_name`
- [x] Encrypted properties: `twilio_account_sid`, `twilio_auth_token`
- [x] Migration `20260703_provisioning` applied

### Slice 2 — SmsService per-institution Twilio ✅
- [x] `_get_twilio_client(account_sid, auth_token)` — uses institution creds with platform fallback
- [x] `send_sms()` passes institution creds already loaded for retention profile

### Slice 3 — Admin API (credential configuration) ✅
- [x] `GET  /admin/institutions/{slug}/provisioning` — masked SID + email fields
- [x] `PATCH /admin/institutions/{slug}/provisioning` — set/update credentials
- [x] `DELETE /admin/institutions/{slug}/provisioning/twilio` — clear Twilio creds
- [x] SUPER_ADMIN (`get_current_admin`) required on all endpoints

### Slice 4 — Tests ✅
- [x] encrypt/decrypt round-trips for Twilio credential fields
- [x] `_mask_sid` helper coverage
- [x] SmsService: uses institution creds when set, falls back to platform
- [x] SmsService: RuntimeError when neither set
- [x] RBAC matrix updated for 3 provisioning routes + 3 outbound-halt routes (Plan 12 gap)
- [x] Tenant scope invariant allowlist line number updated after `_get_twilio_client` expansion

## Files Touched
| File | Change |
|------|--------|
| `src/app/models/institution.py` | Add 4 credential fields + 2 encrypted properties |
| `alembic/versions/20260703_institution_provisioning.py` | New migration |
| `src/app/services/sms_service.py` | Per-institution Twilio client selection |
| `src/app/api/routes/admin_institutions.py` | Provisioning endpoints |
| `tests/unit/test_institution_provisioning.py` | Unit tests |
