# Progress: Outbound 10 — Per-tenant Provisioning

## Initial State
- Platform-level Twilio credentials only; no per-institution Twilio sub-account support
- Platform-level Resend credentials; no per-institution email from-address
- Retell already handled (single enterprise account + per-location retell_agent_id)
- InstitutionLocation.twilio_from_number already exists

## Slices

### Slice 1 — Institution credential fields + migration
- **Status:** complete ✅
- `src/app/models/institution.py` — 4 new fields: `twilio_account_sid_encrypted`, `twilio_auth_token_encrypted`, `email_from_address`, `email_from_name`; 2 encrypted property pairs
- `alembic/versions/20260703_institution_provisioning.py` — revision `20260703_provisioning`, applied to local DB

### Slice 2 — SmsService per-institution Twilio
- **Status:** complete ✅
- `src/app/services/sms_service.py` — `_get_twilio_client(account_sid, auth_token)` with platform fallback; `send_sms()` passes institution creds from already-loaded Institution object

### Slice 3 — Admin provisioning API
- **Status:** complete ✅
- `src/app/api/routes/admin_institutions.py` — GET/PATCH/DELETE provisioning endpoints; `_mask_sid()` helper; `ProvisioningStatusResponse` / `ProvisioningUpdateRequest` Pydantic models

### Slice 4 — Tests
- **Status:** complete ✅
- `tests/unit/test_institution_provisioning.py` — 9 tests: encrypt/decrypt round-trips, mask_sid, SmsService cred selection
- RBAC matrix: 6 missing routes added (3 provisioning + 3 outbound-halt from Plan 12)
- Tenant scope invariant: allowlist line number updated 278→289
- 1165/1165 unit tests passing, no regressions
